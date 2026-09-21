from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from ecomevo.providers.anthropic import AnthropicProvider
from ecomevo.providers.gemini import GeminiProvider
from ecomevo.providers.openai_compat import OpenAICompatProvider
from ecomevo.providers.telemetry import (
    begin_provider_usage_capture,
    current_provider_usage_events,
    record_provider_usage,
    reset_provider_usage_capture,
    summarize_provider_usage,
)


@pytest.mark.asyncio
async def test_openai_compatible_records_provider_reported_usage():
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                    "total_tokens": 150,
                    "prompt_tokens_details": {"cached_tokens": 20},
                    "completion_tokens_details": {"reasoning_tokens": 7},
                },
            },
        )

    provider = OpenAICompatProvider(
        key="openai",
        name="OpenAI",
        vendor="OpenAI",
        api_key="x",
        base_url="https://api.test/v1",
        model="gpt-test",
        multimodal=True,
        transport=httpx.MockTransport(handler),
    )
    token = begin_provider_usage_capture()
    try:
        assert await provider.chat(messages=[{"role": "user", "content": "hi"}]) == "ok"
        events = current_provider_usage_events()
    finally:
        reset_provider_usage_capture(token)

    assert len(events) == 1
    event = events[0]
    assert event["provider"] == "openai"
    assert event["model"] == "gpt-test"
    assert event["input_tokens"] == 120
    assert event["output_tokens"] == 30
    assert event["total_tokens"] == 150
    assert event["cached_input_tokens"] == 20
    assert event["reasoning_tokens"] == 7
    assert event["usage_available"] is True


@pytest.mark.asyncio
async def test_anthropic_records_cache_aware_reported_usage():
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 5,
                },
            },
        )

    provider = AnthropicProvider(
        "x",
        "claude-test",
        transport=httpx.MockTransport(handler),
    )
    token = begin_provider_usage_capture()
    try:
        assert await provider.chat(messages=[{"role": "user", "content": "hi"}]) == "ok"
        events = current_provider_usage_events()
    finally:
        reset_provider_usage_capture(token)

    assert len(events) == 1
    event = events[0]
    assert event["provider"] == "anthropic"
    assert event["input_tokens"] == 100
    assert event["output_tokens"] == 25
    assert event["cache_creation_input_tokens"] == 10
    assert event["cache_read_input_tokens"] == 5
    assert event["total_tokens"] == 140
    assert event["usage_available"] is True


@pytest.mark.asyncio
async def test_gemini_records_usage_metadata():
    def handler(request):
        assert request.url.path.endswith(":generateContent")
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                "usageMetadata": {
                    "promptTokenCount": 80,
                    "candidatesTokenCount": 20,
                    "totalTokenCount": 100,
                    "cachedContentTokenCount": 12,
                    "thoughtsTokenCount": 4,
                },
            },
        )

    provider = GeminiProvider(
        "x",
        "gemini-test",
        transport=httpx.MockTransport(handler),
    )
    token = begin_provider_usage_capture()
    try:
        assert await provider.chat(messages=[{"role": "user", "content": "hi"}]) == "ok"
        events = current_provider_usage_events()
    finally:
        reset_provider_usage_capture(token)

    assert len(events) == 1
    event = events[0]
    assert event["provider"] == "gemini"
    assert event["input_tokens"] == 80
    assert event["output_tokens"] == 20
    assert event["total_tokens"] == 100
    assert event["cached_input_tokens"] == 12
    assert event["reasoning_tokens"] == 4


@pytest.mark.asyncio
async def test_contextvar_usage_capture_is_isolated_between_concurrent_tasks():
    async def capture(provider: str, tokens: int):
        token = begin_provider_usage_capture()
        try:
            await asyncio.sleep(0)
            record_provider_usage(
                provider=provider,
                model=f"{provider}-model",
                source="test",
                usage={
                    "input_tokens": tokens,
                    "output_tokens": 1,
                    "total_tokens": tokens + 1,
                },
            )
            await asyncio.sleep(0)
            return current_provider_usage_events()
        finally:
            reset_provider_usage_capture(token)

    left, right = await asyncio.gather(
        capture("left", 11),
        capture("right", 22),
    )

    assert [row["provider"] for row in left] == ["left"]
    assert [row["provider"] for row in right] == ["right"]
    assert left[0]["input_tokens"] == 11
    assert right[0]["input_tokens"] == 22


def test_exact_price_book_computes_cache_aware_cost(monkeypatch):
    monkeypatch.setenv(
        "ECOMEVO_PROVIDER_PRICING_JSON",
        json.dumps(
            {
                "version": "finance-2026-09-18",
                "currency": "USD",
                "rates": {
                    "openai:gpt-test": {
                        "input_per_million": 2.0,
                        "output_per_million": 4.0,
                        "cached_input_per_million": 1.0,
                        "cached_input_is_subset_of_input": True,
                    }
                },
            }
        ),
    )
    summary = summarize_provider_usage(
        [
            {
                "provider": "openai",
                "model": "gpt-test",
                "purpose": "inference",
                "source": "test",
                "input_tokens": 1000,
                "output_tokens": 500,
                "total_tokens": 1500,
                "cached_input_tokens": 200,
                "cache_creation_input_tokens": None,
                "cache_read_input_tokens": None,
                "reasoning_tokens": None,
                "usage_available": True,
                "total_tokens_derived": False,
            }
        ]
    )

    assert summary["external_calls"] == 1
    assert summary["usage_reported_calls"] == 1
    assert summary["usage_coverage_rate"] == 1.0
    assert summary["cost"]["available"] is True
    assert summary["cost"]["known_amount"] == 0.0038
    assert summary["cost"]["currency"] == "USD"
    assert summary["cost"]["pricing_version"] == "finance-2026-09-18"
    assert summary["cost"]["priced_calls"] == 1


def test_missing_exact_price_rule_keeps_known_subset_but_not_total(monkeypatch):
    monkeypatch.setenv(
        "ECOMEVO_PROVIDER_PRICING_JSON",
        json.dumps(
            {
                "version": "finance-v1",
                "currency": "USD",
                "rates": {
                    "openai:gpt-priced": {
                        "input_per_million": 1.0,
                        "output_per_million": 2.0,
                    }
                },
            }
        ),
    )
    events = []
    for model in ("gpt-priced", "gpt-unpriced"):
        events.append(
            {
                "provider": "openai",
                "model": model,
                "purpose": "inference",
                "source": "test",
                "input_tokens": 1000,
                "output_tokens": 1000,
                "total_tokens": 2000,
                "cached_input_tokens": None,
                "cache_creation_input_tokens": None,
                "cache_read_input_tokens": None,
                "reasoning_tokens": None,
                "usage_available": True,
                "total_tokens_derived": False,
            }
        )
    summary = summarize_provider_usage(events)

    assert summary["external_calls"] == 2
    assert summary["usage_reported_calls"] == 2
    assert summary["cost"]["available"] is False
    assert summary["cost"]["known_amount"] == 0.003
    assert summary["cost"]["priced_calls"] == 1
    assert summary["cost"]["coverage_rate"] == 0.5
    assert "no complete exact price rule" in summary["cost"]["reason"]


@pytest.mark.asyncio
async def test_successful_provider_call_without_usage_is_not_estimated():
    def handler(_request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    provider = OpenAICompatProvider(
        key="custom",
        name="Custom",
        vendor="Custom",
        api_key="x",
        base_url="https://api.test/v1",
        model="custom-model",
        multimodal=False,
        transport=httpx.MockTransport(handler),
    )
    token = begin_provider_usage_capture()
    try:
        assert await provider.chat(messages=[{"role": "user", "content": "hi"}]) == "ok"
        summary = summarize_provider_usage(current_provider_usage_events())
    finally:
        reset_provider_usage_capture(token)

    assert summary["external_calls"] == 1
    assert summary["usage_reported_calls"] == 0
    assert summary["input_tokens"] == 0
    assert summary["output_tokens"] == 0
    assert summary["total_tokens"] == 0
    assert summary["cost"]["available"] is False
