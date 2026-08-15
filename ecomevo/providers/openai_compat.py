from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from .base import BaseProvider, ProviderError, ProviderInfo


def _data_url(path: str, mime: str | None = None) -> str:
    p = Path(path)
    mime = mime or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


class OpenAICompatProvider(BaseProvider):
    def __init__(self, *, key: str, name: str, vendor: str, api_key: str | None, base_url: str,
                 model: str | None, multimodal: bool, note: str = "", extra_headers: dict[str, str] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.extra_headers = extra_headers or {}
        self.transport = transport
        self.info = ProviderInfo(
            key=key, name=name, vendor=vendor, model=model,
            configured=bool(api_key and model), multimodal=multimodal, note=note,
        )

    async def chat(self, *, messages: list[dict[str, Any]], assets: list[dict[str, Any]] | None = None,
                   temperature: float = 0.2, max_tokens: int = 1400) -> str:
        if not self.info.configured:
            raise ProviderError(f"{self.info.name} 未配置")
        out = [dict(m) for m in messages]
        image_assets = [a for a in (assets or []) if str(a.get("mime", "")).startswith("image/")]
        if image_assets and self.info.multimodal and out:
            last = out[-1]
            if last.get("role") == "user":
                content: list[dict[str, Any]] = [{"type": "text", "text": str(last.get("content", ""))}]
                for a in image_assets[:6]:
                    content.append({"type": "image_url", "image_url": {"url": _data_url(a["path"], a.get("mime"))}})
                out[-1] = {"role": "user", "content": content}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", **self.extra_headers}
        payload = {"model": self.model, "messages": out, "temperature": temperature}
        # OpenAI's current Chat Completions API deprecates max_tokens in favor of max_completion_tokens;
        # many OpenAI-compatible vendors still require max_tokens, so only switch the native OpenAI adapter.
        payload["max_completion_tokens" if self.info.key == "openai" else "max_tokens"] = max_tokens
        async with httpx.AsyncClient(timeout=90,transport=self.transport) as client:
            r = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            # Some reasoning models reject sampling temperature. A 400 is non-executing, so retry once without it.
            if self.info.key == "openai" and r.status_code == 400 and "temperature" in r.text.lower():
                payload.pop("temperature",None)
                r = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        if r.status_code >= 400:
            raise ProviderError(f"{self.info.name} 请求失败 {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except Exception as exc:
            raise ProviderError(f"{self.info.name} 返回格式异常") from exc
