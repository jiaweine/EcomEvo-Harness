from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class ProviderInfo:
    key: str
    name: str
    vendor: str
    model: str | None
    configured: bool
    multimodal: bool
    supports_video: bool = False
    supports_audio: bool = False
    note: str = ""
    supports_document: bool = False

    def dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderError(RuntimeError):
    pass


class BaseProvider:
    info: ProviderInfo

    async def chat(self, *, messages: list[dict[str, Any]], assets: list[dict[str, Any]] | None = None,
                   temperature: float = 0.2, max_tokens: int = 1400) -> str:
        raise NotImplementedError
