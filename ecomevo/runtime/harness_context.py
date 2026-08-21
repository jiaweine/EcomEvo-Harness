from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any


_CURRENT_HARNESS_PROFILE: ContextVar[dict[str, Any]] = ContextVar(
    "ecomevo_harness_profile", default={}
)


def bind_harness_profile(profile: dict[str, Any]) -> Token:
    return _CURRENT_HARNESS_PROFILE.set(profile or {})


def reset_harness_profile(token: Token) -> None:
    _CURRENT_HARNESS_PROFILE.reset(token)


def current_harness_profile() -> dict[str, Any]:
    return _CURRENT_HARNESS_PROFILE.get() or {}
