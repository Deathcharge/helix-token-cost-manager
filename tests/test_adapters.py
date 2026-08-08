# Copyright 2026 Samsarix LLC
# SPDX-License-Identifier: Apache-2.0

"""Compatibility fixtures for dependency-free external usage adapters."""

from types import SimpleNamespace

import pytest

from samsarix_token_cost_manager import (
    ValidationError,
    from_anthropic_response,
    from_openai_response,
    from_otel_attributes,
)


def test_openai_responses_usage_normalizes_inclusive_cache_tokens() -> None:
    measurement = from_openai_response(
        {
            "id": "resp_123",
            "model": "gpt-5-mini-2025-08-07",
            "service_tier": "default",
            "usage": {
                "input_tokens": 1_000,
                "output_tokens": 250,
                "input_tokens_details": {"cached_tokens": 800},
            },
        }
    )

    assert measurement.to_dict() == {
        "provider": "openai",
        "model": "gpt-5-mini-2025-08-07",
        "input_tokens": 200,
        "output_tokens": 250,
        "cached_input_tokens": 800,
        "cache_write_input_tokens": 0,
        "request_id": "resp_123",
        "dimensions": {"service_tier": "default"},
    }


def test_openai_chat_object_supports_legacy_usage_names() -> None:
    response = SimpleNamespace(
        id="chatcmpl_123",
        model="gpt-4o-mini",
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=5),
    )

    measurement = from_openai_response(response)

    assert measurement.input_tokens == 20
    assert measurement.output_tokens == 5
    assert measurement.cached_input_tokens == 0
    assert measurement.dimensions == ()


def test_anthropic_usage_preserves_mutually_exclusive_cache_buckets() -> None:
    measurement = from_anthropic_response(
        {
            "id": "msg_123",
            "model": "claude-sonnet-5",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 40,
                "cache_read_input_tokens": 500,
                "cache_creation_input_tokens": 200,
                "service_tier": "standard_only",
            },
        }
    )

    assert measurement.input_tokens == 100
    assert measurement.cached_input_tokens == 500
    assert measurement.cache_write_input_tokens == 200
    assert measurement.dimensions == (("service_tier", "standard_only"),)


def test_otel_attributes_normalize_usage_and_allocation_context() -> None:
    measurement = from_otel_attributes(
        {
            "gen_ai.provider.name": "openai",
            "gen_ai.request.model": "requested-model",
            "gen_ai.response.model": "resolved-model",
            "gen_ai.response.id": "resp_otel",
            "gen_ai.usage.input_tokens": 1_000,
            "gen_ai.usage.output_tokens": 80,
            "gen_ai.usage.cache_read.input_tokens": 600,
            "gen_ai.usage.cache_creation.input_tokens": 100,
            "gen_ai.conversation.id": "conversation-1",
            "gen_ai.agent.name": "support-agent",
            "gen_ai.agent.version": "2.1",
            "gen_ai.workflow.name": "ticket-resolution",
            "service.name": "support-api",
            "deployment.environment.name": "production",
        }
    )

    assert measurement.model == "resolved-model"
    assert measurement.input_tokens == 300
    assert measurement.output_tokens == 80
    assert measurement.cached_input_tokens == 600
    assert measurement.cache_write_input_tokens == 100
    assert dict(measurement.dimensions) == {
        "agent": "support-agent",
        "agent_version": "2.1",
        "conversation": "conversation-1",
        "environment": "production",
        "service": "support-api",
        "workflow": "ticket-resolution",
    }


def test_otel_falls_back_to_request_model_and_optional_dimensions() -> None:
    measurement = from_otel_attributes(
        {
            "gen_ai.provider.name": "anthropic",
            "gen_ai.request.model": "claude-haiku-4-5",
            "gen_ai.usage.input_tokens": 5,
            "gen_ai.usage.output_tokens": 2,
        }
    )

    assert measurement.model == "claude-haiku-4-5"
    assert measurement.request_id is None
    assert measurement.dimensions == ()


@pytest.mark.parametrize(
    ("adapter", "payload", "message"),
    [
        (
            from_openai_response,
            {
                "model": "gpt",
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 1,
                    "input_tokens_details": {"cached_tokens": 3},
                },
            },
            "cached_input_tokens may not exceed",
        ),
        (
            from_otel_attributes,
            {
                "gen_ai.provider.name": "openai",
                "gen_ai.request.model": "gpt",
                "gen_ai.usage.input_tokens": 2,
                "gen_ai.usage.output_tokens": 1,
                "gen_ai.usage.cache_read.input_tokens": 2,
                "gen_ai.usage.cache_creation.input_tokens": 1,
            },
            "cache read and creation tokens may not exceed",
        ),
        (from_openai_response, {"model": "gpt"}, "missing 'usage'"),
        (from_otel_attributes, [], "must be a mapping"),
    ],
)
def test_adapter_errors_are_actionable(adapter: object, payload: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        adapter(payload)  # type: ignore[operator]
