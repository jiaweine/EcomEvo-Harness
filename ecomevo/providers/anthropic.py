from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from .base import BaseProvider, ProviderError, ProviderInfo


class AnthropicProvider(BaseProvider):
    def __init__(self, api_key: str | None, model: str | None):
        self.api_key, self.model = api_key, model
        self.info = ProviderInfo("anthropic", "Claude", "Anthropic", model, bool(api_key and model), True,
                                 note="适合长文档、图片理解与复杂分析")

    async def chat(self, *, messages: list[dict[str, Any]], assets: list[dict[str, Any]] | None = None,
                   temperature: float = 0.2, max_tokens: int = 1400) -> str:
        if not self.info.configured:
            raise ProviderError("Claude 未配置")
        system = ""
        msgs = []
        for m in messages:
            if m.get("role") == "system":
                system += str(m.get("content", "")) + "\n"
            else:
                msgs.append({"role": m.get("role", "user"), "content": str(m.get("content", ""))})
        imgs = [a for a in (assets or []) if str(a.get("mime", "")).startswith("image/")]
        if imgs and msgs and msgs[-1]["role"] == "user":
            blocks: list[dict[str, Any]] = [{"type": "text", "text": msgs[-1]["content"]}]
            for a in imgs[:6]:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": a.get("mime", "image/png"),
                                                             "data": base64.b64encode(Path(a["path"]).read_bytes()).decode()}})
            msgs[-1]["content"] = blocks
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        payload = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature, "messages": msgs}
        if system.strip(): payload["system"] = system.strip()
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
        if r.status_code >= 400:
            raise ProviderError(f"Claude 请求失败 {r.status_code}: {r.text[:300]}")
        data = r.json()
        return "\n".join(x.get("text", "") for x in data.get("content", []) if x.get("type") == "text")
