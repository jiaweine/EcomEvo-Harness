from __future__ import annotations

import json
import math
import os
from contextvars import ContextVar, Token
from typing import Any


SCHEMA_VERSION = 1
_USAGE_EVENTS: ContextVar[tuple[dict[str, Any], ...] | None] = ContextVar(
    "ecomevo_provider_usage_events",
    default=None,
)
_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "reasoning_tokens",
)


def _nonnegative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _nonnegative_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def normalize_openai_usage(raw: Any) -> dict[str, Any]:
    usage = raw if isinstance(raw, dict) else {}
    prompt_details = usage.get("prompt_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    completion_details = usage.get("completion_tokens_details")
    completion_details = completion_details if isinstance(completion_details, dict) else {}
    input_tokens = _nonnegative_int(usage.get("prompt_tokens"))
    output_tokens = _nonnegative_int(usage.get("completion_tokens"))
    total_tokens = _nonnegative_int(usage.get("total_tokens"))
    derived = False
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
        derived = True
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": _nonnegative_int(prompt_details.get("cached_tokens")),
        "reasoning_tokens": _nonnegative_int(completion_details.get("reasoning_tokens")),
        "total_tokens_derived": derived,
    }


def normalize_anthropic_usage(raw: Any) -> dict[str, Any]:
    usage = raw if isinstance(raw, dict) else {}
    input_tokens = _nonnegative_int(usage.get("input_tokens"))
    output_tokens = _nonnegative_int(usage.get("output_tokens"))
    cache_creation = _nonnegative_int(usage.get("cache_creation_input_tokens"))
    cache_read = _nonnegative_int(usage.get("cache_read_input_tokens"))
    components = [input_tokens, output_tokens, cache_creation, cache_read]
    total_tokens = sum(value or 0 for value in components) if any(value is not None for value in components) else None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "total_tokens_derived": total_tokens is not None,
    }


def normalize_gemini_usage(raw: Any) -> dict[str, Any]:
    usage = raw if isinstance(raw, dict) else {}
    input_tokens = _nonnegative_int(usage.get("promptTokenCount"))
    output_tokens = _nonnegative_int(usage.get("candidatesTokenCount"))
    total_tokens = _nonnegative_int(usage.get("totalTokenCount"))
    derived = False
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
        derived = True
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": _nonnegative_int(usage.get("cachedContentTokenCount")),
        "reasoning_tokens": _nonnegative_int(usage.get("thoughtsTokenCount")),
        "total_tokens_derived": derived,
    }


def begin_provider_usage_capture() -> Token:
    return _USAGE_EVENTS.set(())


def current_provider_usage_events() -> list[dict[str, Any]]:
    rows = _USAGE_EVENTS.get()
    return [dict(row) for row in (rows or ())]


def reset_provider_usage_capture(token: Token) -> None:
    _USAGE_EVENTS.reset(token)


def record_provider_usage(
    *,
    provider: str,
    model: str | None,
    source: str,
    usage: dict[str, Any] | None,
    purpose: str = "inference",
) -> None:
    """Append a sanitized usage event when a capture scope is active.

    This function is intentionally best-effort. Provider success must never turn into
    a user-visible failure because telemetry parsing or capture is unavailable.
    """
    try:
        current = _USAGE_EVENTS.get()
        if current is None:
            return
        clean_usage = usage if isinstance(usage, dict) else {}
        event: dict[str, Any] = {
            "provider": str(provider or "unknown")[:64],
            "model": (str(model)[:256] if model else None),
            "purpose": str(purpose or "inference")[:64],
            "source": str(source or "unknown")[:128],
        }
        for field in _TOKEN_FIELDS:
            event[field] = _nonnegative_int(clean_usage.get(field))
        event["total_tokens_derived"] = bool(clean_usage.get("total_tokens_derived"))
        event["usage_available"] = any(
            event.get(field) is not None
            for field in ("input_tokens", "output_tokens", "total_tokens")
        )
        _USAGE_EVENTS.set((*current, event))
    except Exception:
        return


def _load_price_book() -> dict[str, Any]:
    raw = os.environ.get("ECOMEVO_PROVIDER_PRICING_JSON", "").strip()
    if not raw:
        return {
            "configured": False,
            "valid": False,
            "reason": "provider price book is not configured",
            "version": None,
            "currency": None,
            "rates": {},
        }
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "configured": True,
            "valid": False,
            "reason": "provider price book is invalid JSON",
            "version": None,
            "currency": None,
            "rates": {},
        }
    if not isinstance(payload, dict):
        return {
            "configured": True,
            "valid": False,
            "reason": "provider price book must be an object",
            "version": None,
            "currency": None,
            "rates": {},
        }
    version = str(payload.get("version") or "").strip()
    currency = str(payload.get("currency") or "").strip().upper()
    rates = payload.get("rates")
    if not version or len(currency) != 3 or not currency.isalpha() or not isinstance(rates, dict):
        return {
            "configured": True,
            "valid": False,
            "reason": "provider price book requires version, 3-letter currency, and rates",
            "version": version or None,
            "currency": currency or None,
            "rates": {},
        }
    clean_rates: dict[str, dict[str, Any]] = {}
    for key, raw_rule in rates.items():
        if not isinstance(key, str) or ":" not in key or not isinstance(raw_rule, dict):
            continue
        rule: dict[str, Any] = {}
        for field in (
            "input_per_million",
            "output_per_million",
            "cached_input_per_million",
            "cache_creation_input_per_million",
            "cache_read_input_per_million",
        ):
            value = _nonnegative_float(raw_rule.get(field))
            if value is not None:
                rule[field] = value
        if isinstance(raw_rule.get("cached_input_is_subset_of_input"), bool):
            rule["cached_input_is_subset_of_input"] = raw_rule["cached_input_is_subset_of_input"]
        if "input_per_million" in rule and "output_per_million" in rule:
            clean_rates[key] = rule
    if not clean_rates:
        return {
            "configured": True,
            "valid": False,
            "reason": "provider price book has no valid exact provider:model rates",
            "version": version,
            "currency": currency,
            "rates": {},
        }
    return {
        "configured": True,
        "valid": True,
        "reason": None,
        "version": version,
        "currency": currency,
        "rates": clean_rates,
    }


def _event_cost(event: dict[str, Any], price_book: dict[str, Any]) -> dict[str, Any]:
    if not event.get("usage_available"):
        return {"available": False, "reason": "provider response did not report token usage"}
    if not price_book.get("valid"):
        return {"available": False, "reason": price_book.get("reason") or "provider price book unavailable"}
    model = str(event.get("model") or "")
    key = f"{event.get('provider')}:{model}"
    rule = (price_book.get("rates") or {}).get(key)
    if not isinstance(rule, dict):
        return {"available": False, "reason": f"no exact price rule for {key}"}

    input_tokens = event.get("input_tokens")
    output_tokens = event.get("output_tokens")
    if input_tokens is None or output_tokens is None:
        return {"available": False, "reason": "input/output token counts are incomplete"}

    amount = 0.0
    cached_input = int(event.get("cached_input_tokens") or 0)
    if cached_input:
        cached_rate = rule.get("cached_input_per_million")
        subset = rule.get("cached_input_is_subset_of_input")
        if cached_rate is None or not isinstance(subset, bool):
            return {
                "available": False,
                "reason": "cached token pricing requires cached_input_per_million and cached_input_is_subset_of_input",
            }
        uncached_input = int(input_tokens) - cached_input if subset else int(input_tokens)
        if uncached_input < 0:
            return {"available": False, "reason": "cached input tokens exceed input tokens"}
        amount += uncached_input * float(rule["input_per_million"]) / 1_000_000.0
        amount += cached_input * float(cached_rate) / 1_000_000.0
    else:
        amount += int(input_tokens) * float(rule["input_per_million"]) / 1_000_000.0

    cache_creation = int(event.get("cache_creation_input_tokens") or 0)
    if cache_creation:
        rate = rule.get("cache_creation_input_per_million")
        if rate is None:
            return {"available": False, "reason": "cache creation token price is missing"}
        amount += cache_creation * float(rate) / 1_000_000.0

    cache_read = int(event.get("cache_read_input_tokens") or 0)
    if cache_read:
        rate = rule.get("cache_read_input_per_million")
        if rate is None:
            return {"available": False, "reason": "cache read token price is missing"}
        amount += cache_read * float(rate) / 1_000_000.0

    amount += int(output_tokens) * float(rule["output_per_million"]) / 1_000_000.0
    return {
        "available": True,
        "amount": round(amount, 10),
        "currency": price_book["currency"],
        "pricing_version": price_book["version"],
        "price_key": key,
    }


def empty_provider_usage(reason: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "external_calls": 0,
        "usage_reported_calls": 0,
        "usage_coverage_rate": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "events": [],
        "cost": {
            "available": False,
            "known_amount": 0.0,
            "currency": None,
            "pricing_version": None,
            "priced_calls": 0,
            "coverage_rate": None,
            "reason": reason or "no external provider calls",
        },
    }


def summarize_provider_usage(events: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        clean_events = [dict(row) for row in events if isinstance(row, dict)]
        if not clean_events:
            return empty_provider_usage()

        price_book = _load_price_book()
        usage_calls = sum(bool(row.get("usage_available")) for row in clean_events)
        input_tokens = sum(int(row.get("input_tokens") or 0) for row in clean_events if row.get("usage_available"))
        output_tokens = sum(int(row.get("output_tokens") or 0) for row in clean_events if row.get("usage_available"))
        total_tokens = sum(int(row.get("total_tokens") or 0) for row in clean_events if row.get("usage_available"))

        priced_calls = 0
        known_amount = 0.0
        event_rows: list[dict[str, Any]] = []
        for event in clean_events:
            event_cost = _event_cost(event, price_book)
            row = dict(event)
            row["cost"] = event_cost
            event_rows.append(row)
            if event_cost.get("available"):
                priced_calls += 1
                known_amount += float(event_cost.get("amount") or 0.0)

        external_calls = len(clean_events)
        usage_coverage = round(usage_calls / external_calls, 4) if external_calls else None
        price_coverage = round(priced_calls / external_calls, 4) if external_calls else None
        fully_priced = external_calls > 0 and priced_calls == external_calls

        if fully_priced:
            cost_reason = None
        elif not price_book.get("valid"):
            cost_reason = price_book.get("reason") or "provider price book unavailable"
        elif usage_calls < external_calls:
            cost_reason = "some provider calls did not report token usage"
        else:
            cost_reason = "some provider/model calls have no complete exact price rule"

        return {
            "schema_version": SCHEMA_VERSION,
            "external_calls": external_calls,
            "usage_reported_calls": usage_calls,
            "usage_coverage_rate": usage_coverage,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "events": event_rows,
            "cost": {
                "available": fully_priced,
                "known_amount": round(known_amount, 10),
                "currency": price_book.get("currency"),
                "pricing_version": price_book.get("version"),
                "priced_calls": priced_calls,
                "coverage_rate": price_coverage,
                "reason": cost_reason,
            },
        }
    except Exception:
        return empty_provider_usage("provider usage telemetry aggregation failed")
