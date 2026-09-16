from __future__ import annotations

import contextvars
from typing import Any


_policy_scope: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "ecomevo_policy_scope",
    default={},
)


def current_policy_scope() -> dict[str, str]:
    return dict(_policy_scope.get() or {})


def bind_policy_scope(scope: dict[str, Any] | None):
    clean = {
        str(key).strip(): str(value).strip()
        for key, value in dict(scope or {}).items()
        if str(key).strip() and str(value).strip()
    }
    return _policy_scope.set(clean)


def reset_policy_scope(token) -> None:
    _policy_scope.reset(token)
