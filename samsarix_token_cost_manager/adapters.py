# Copyright 2026 Samsarix LLC
# SPDX-License-Identifier: Apache-2.0

"""Dependency-free adapters for provider responses and OpenTelemetry attributes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .exceptions import ValidationError
from .models import validated_dimensions, validated_text, validated_tokens

_MISSING = object()


@dataclass(frozen=True)
class UsageMeasurement:
    """Normalized mutually exclusive usage extracted from an external payload."""

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    request_id: Optional[str] = None
    dimensions: Tuple[Tuple[str, str], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe normalized representation."""

        return {
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "request_id": self.request_id,
            "dimensions": dict(self.dimensions),
        }


def _field(value: object, name: str, default: object = _MISSING) -> Any:
    if isinstance(value, Mapping):
        if name in value:
            return value[name]
    elif hasattr(value, name):
        return getattr(value, name)
    if default is not _MISSING:
        return default
    raise ValidationError(f"external usage payload is missing {name!r}")


def _first(value: object, *names: str, default: object = _MISSING) -> Any:
    for name in names:
        try:
            result = _field(value, name)
        except ValidationError:
            continue
        if result is not None:
            return result
    if default is not _MISSING:
        return default
    raise ValidationError(f"external usage payload is missing one of {', '.join(names)}")


def _optional_text(value: object, *, field: str) -> Optional[str]:
    if value is None:
        return None
    return validated_text(value, field=field)


def _measurement(
    *,
    provider: object,
    model: object,
    input_tokens: object,
    output_tokens: object,
    cached_input_tokens: object = 0,
    cache_write_input_tokens: object = 0,
    request_id: object = None,
    dimensions: Optional[Mapping[str, str]] = None,
) -> UsageMeasurement:
    return UsageMeasurement(
        provider=validated_text(provider, field="provider"),
        model=validated_text(model, field="model"),
        input_tokens=validated_tokens(input_tokens, field="input_tokens"),
        output_tokens=validated_tokens(output_tokens, field="output_tokens"),
        cached_input_tokens=validated_tokens(cached_input_tokens, field="cached_input_tokens"),
        cache_write_input_tokens=validated_tokens(
            cache_write_input_tokens, field="cache_write_input_tokens"
        ),
        request_id=_optional_text(request_id, field="request_id"),
        dimensions=validated_dimensions(dimensions),
    )


def from_openai_response(response: object) -> UsageMeasurement:
    """Normalize an OpenAI Responses or Chat Completions response.

    OpenAI reports cached input as a subset of total input, so the adapter
    subtracts it to satisfy this package's mutually exclusive token buckets.
    """

    usage = _field(response, "usage")
    input_total = validated_tokens(
        _first(usage, "input_tokens", "prompt_tokens"), field="input_tokens"
    )
    output_tokens = _first(usage, "output_tokens", "completion_tokens")
    details = _first(
        usage,
        "input_tokens_details",
        "prompt_tokens_details",
        default={},
    )
    cached_tokens = validated_tokens(
        _field(details, "cached_tokens", 0), field="cached_input_tokens"
    )
    if cached_tokens > input_total:
        raise ValidationError("cached_input_tokens may not exceed inclusive input_tokens")
    dimensions: Dict[str, str] = {}
    service_tier = _field(response, "service_tier", None)
    if service_tier is not None:
        dimensions["service_tier"] = validated_text(service_tier, field="service_tier")
    return _measurement(
        provider="openai",
        model=_field(response, "model"),
        input_tokens=input_total - cached_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_tokens,
        request_id=_field(response, "id", None),
        dimensions=dimensions,
    )


def from_anthropic_response(response: object) -> UsageMeasurement:
    """Normalize an Anthropic Messages response, including cache reads/writes."""

    usage = _field(response, "usage")
    dimensions: Dict[str, str] = {}
    service_tier = _field(usage, "service_tier", None)
    if service_tier is not None:
        dimensions["service_tier"] = validated_text(service_tier, field="service_tier")
    return _measurement(
        provider="anthropic",
        model=_field(response, "model"),
        input_tokens=_field(usage, "input_tokens"),
        output_tokens=_field(usage, "output_tokens"),
        cached_input_tokens=_field(usage, "cache_read_input_tokens", 0),
        cache_write_input_tokens=_field(usage, "cache_creation_input_tokens", 0),
        request_id=_field(response, "id", None),
        dimensions=dimensions,
    )


def from_otel_attributes(attributes: Mapping[str, object]) -> UsageMeasurement:
    """Normalize OpenTelemetry GenAI span attributes without storing content.

    ``gen_ai.usage.input_tokens`` is treated as inclusive of cache reads and
    cache creation; the returned buckets are mutually exclusive.
    """

    if not isinstance(attributes, Mapping):
        raise ValidationError("OpenTelemetry attributes must be a mapping")
    input_total = validated_tokens(
        _field(attributes, "gen_ai.usage.input_tokens"), field="input_tokens"
    )
    cache_read = validated_tokens(
        _field(attributes, "gen_ai.usage.cache_read.input_tokens", 0),
        field="cached_input_tokens",
    )
    cache_write = validated_tokens(
        _field(attributes, "gen_ai.usage.cache_creation.input_tokens", 0),
        field="cache_write_input_tokens",
    )
    if cache_read + cache_write > input_total:
        raise ValidationError(
            "cache read and creation tokens may not exceed inclusive input tokens"
        )
    dimensions: Dict[str, str] = {}
    dimension_attributes = {
        "conversation": "gen_ai.conversation.id",
        "agent": "gen_ai.agent.name",
        "agent_version": "gen_ai.agent.version",
        "workflow": "gen_ai.workflow.name",
        "service": "service.name",
        "environment": "deployment.environment.name",
    }
    for dimension, attribute in dimension_attributes.items():
        raw_value = _field(attributes, attribute, None)
        if raw_value is not None:
            dimensions[dimension] = validated_text(raw_value, field=attribute)
    return _measurement(
        provider=_field(attributes, "gen_ai.provider.name"),
        model=_first(attributes, "gen_ai.response.model", "gen_ai.request.model"),
        input_tokens=input_total - cache_read - cache_write,
        output_tokens=_field(attributes, "gen_ai.usage.output_tokens"),
        cached_input_tokens=cache_read,
        cache_write_input_tokens=cache_write,
        request_id=_field(attributes, "gen_ai.response.id", None),
        dimensions=dimensions,
    )
