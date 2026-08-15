from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

import httpx

from .base import BaseProvider, ProviderError, ProviderInfo


class GeminiProvider(BaseProvider):
    """Gemini generateContent adapter with native multimodal/file support.

    Small media is sent inline. Larger audio/video/images use the Files API so
    they are not silently dropped. PDFs are supported up to Gemini's 50 MB
    document limit; larger PDFs are rejected explicitly rather than ignored.
    """

    PDF_LIMIT = 50 * 1024 * 1024

    def __init__(self, api_key: str | None, model: str | None, *,
                 transport: httpx.AsyncBaseTransport | None = None,
                 inline_limit: int = 32 * 1024 * 1024):
        self.api_key, self.model = api_key, model
        self.transport = transport
        self.inline_limit = max(1, int(inline_limit))
        self.info = ProviderInfo(
            key="gemini", name="Gemini", vendor="Google", model=model,
            configured=bool(api_key and model), multimodal=True,
            supports_video=True, supports_audio=True,
            note="支持图片、视频、音频和 PDF 文档理解",
            supports_document=True,
        )

    @staticmethod
    def _supported_mime(mime: str) -> bool:
        return mime.startswith(("image/", "audio/", "video/")) or mime == "application/pdf"

    async def _upload_file(self, client: httpx.AsyncClient, *, path: Path, mime: str, display_name: str) -> dict[str, str]:
        size = path.stat().st_size
        start_headers = {
            "x-goog-api-key": str(self.api_key),
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
            "Content-Type": "application/json",
        }
        start = await client.post(
            "https://generativelanguage.googleapis.com/upload/v1beta/files",
            headers=start_headers,
            json={"file": {"display_name": display_name[:512]}},
        )
        if start.status_code >= 400:
            raise ProviderError(f"Gemini 文件上传初始化失败 {start.status_code}: {start.text[:240]}")
        upload_url = start.headers.get("x-goog-upload-url")
        if not upload_url:
            raise ProviderError("Gemini 文件上传未返回 resumable URL")

        async def body_stream():
            # AsyncClient requires an async byte stream. Reading bounded chunks
            # avoids materializing a second copy of a large media file in RAM.
            with path.open("rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk

        uploaded = await client.post(
            upload_url,
            headers={
                "Content-Length": str(size),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
                "Content-Type": mime,
            },
            content=body_stream(),
        )
        if uploaded.status_code >= 400:
            raise ProviderError(f"Gemini 文件上传失败 {uploaded.status_code}: {uploaded.text[:240]}")
        try:
            file_info = uploaded.json().get("file") or {}
        except Exception as exc:
            raise ProviderError("Gemini 文件上传返回格式异常") from exc
        name = str(file_info.get("name") or "")
        uri = str(file_info.get("uri") or "")
        state = str(file_info.get("state") or "ACTIVE").upper()
        if not name or not uri:
            raise ProviderError("Gemini 文件上传缺少 file name/uri")

        # Video and some other media can remain PROCESSING after upload. Poll the
        # file resource before using it; fail closed on FAILED/timeout.
        for _ in range(30):
            if state in {"ACTIVE", "SUCCEEDED", "STATE_UNSPECIFIED", ""}:
                break
            if state in {"FAILED", "ERROR"}:
                raise ProviderError("Gemini 文件处理失败")
            await asyncio.sleep(1.5)
            status = await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/{name}",
                headers={"x-goog-api-key": str(self.api_key)},
            )
            if status.status_code >= 400:
                raise ProviderError(f"Gemini 文件状态查询失败 {status.status_code}: {status.text[:240]}")
            info = status.json()
            state = str(info.get("state") or "").upper()
            uri = str(info.get("uri") or uri)
        else:
            raise ProviderError("Gemini 文件处理超时")
        return {"name": name, "uri": uri, "mime": mime}

    async def _delete_file(self, client: httpx.AsyncClient, name: str) -> None:
        try:
            await client.delete(
                f"https://generativelanguage.googleapis.com/v1beta/{name}",
                headers={"x-goog-api-key": str(self.api_key)},
            )
        except Exception:
            # Cleanup is best-effort and must not turn a completed inference into
            # a user-facing failure.
            pass

    async def chat(self, *, messages: list[dict[str, Any]], assets: list[dict[str, Any]] | None = None,
                   temperature: float = 0.2, max_tokens: int = 1400) -> str:
        if not self.info.configured:
            raise ProviderError("Gemini 未配置")
        text = "\n".join(f"{m.get('role','user')}: {m.get('content','')}" for m in messages)
        parts: list[dict[str, Any]] = [{"text": text}]
        uploaded_names: list[str] = []
        timeout = httpx.Timeout(180.0, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
            try:
                for a in (assets or [])[:8]:
                    mime = str(a.get("mime", ""))
                    if not self._supported_mime(mime):
                        continue
                    path = Path(str(a.get("path") or ""))
                    if not path.is_file():
                        raise ProviderError(f"Gemini 附件不存在: {a.get('name') or path.name}")
                    size = path.stat().st_size
                    if mime == "application/pdf" and size > self.PDF_LIMIT:
                        raise ProviderError("Gemini PDF 文档超过 50MB，无法进行页面级理解")
                    if size <= self.inline_limit:
                        parts.append({
                            "inline_data": {
                                "mime_type": mime,
                                "data": base64.b64encode(path.read_bytes()).decode(),
                            }
                        })
                    else:
                        remote = await self._upload_file(
                            client, path=path, mime=mime,
                            display_name=str(a.get("name") or path.name),
                        )
                        uploaded_names.append(remote["name"])
                        parts.append({"file_data": {"mime_type": mime, "file_uri": remote["uri"]}})

                url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
                payload = {
                    "contents": [{"role": "user", "parts": parts}],
                    "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
                }
                r = await client.post(
                    url,
                    headers={"x-goog-api-key": str(self.api_key), "Content-Type": "application/json"},
                    json=payload,
                )
                if r.status_code >= 400:
                    raise ProviderError(f"Gemini 请求失败 {r.status_code}: {r.text[:300]}")
                data = r.json()
                try:
                    return "\n".join(
                        x.get("text", "")
                        for x in data["candidates"][0]["content"]["parts"]
                        if "text" in x
                    )
                except Exception as exc:
                    raise ProviderError("Gemini 返回格式异常") from exc
            finally:
                for name in uploaded_names:
                    await self._delete_file(client, name)
