# Copyright 2026 Samsarix LLC
# SPDX-License-Identifier: Apache-2.0

"""Validated public value objects used by the cost manager."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Dict, Optional, Tuple, Union

from .exceptions import ValidationError

DecimalInput = Union[Decimal, int, str, float]

MAX_TEXT_LENGTH = 200
MAX_DIMENSIONS = 32
MAX_TOKENS = 1_000_000_000_000
MAX_RATE_USD_PER_MILLION = Decimal("1000000")
USD_QUANTUM = Decimal("0.000000000001")


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(timezone.utc)


def parse_timestamp(value: object, *, field: str) -> datetime:
    """Parse an ISO-8601 date or timestamp and normalize it to UTC."""

    if value is None:
        return utc_now()
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValidationError(f"{field} must not be empty")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValidationError(f"{field} must be an ISO-8601 date or timestamp") from exc
    else:
        raise ValidationError(f"{field} must be an ISO-8601 date or timestamp")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_timestamp(value: datetime) -> str:
    """Serialize an aware timestamp in a stable UTC form."""

    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def validated_text(
    value: object,
    *,
    field: str,
    required: bool = True,
    reserved: Optional[str] = None,
) -> str:
    """Validate and normalize a bounded identifier-like string."""

    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    normalized = value.strip()
    if required and not normalized:
        raise ValidationError(f"{field} must not be empty")
    if len(normalized) > MAX_TEXT_LENGTH:
        raise ValidationError(f"{field} must be at most {MAX_TEXT_LENGTH} characters")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in normalized):
        raise ValidationError(
            f"{field} must be a single line without NUL, control, or formatting characters"
        )
    if reserved is not None and normalized == reserved:
        raise ValidationError(f"{field} may not be {reserved!r}")
    return normalized


def validated_tokens(value: object, *, field: str) -> int:
    """Validate a non-negative, bounded token count."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be an integer")
    if value < 0:
        raise ValidationError(f"{field} must be non-negative")
    if value > MAX_TOKENS:
        raise ValidationError(f"{field} must not exceed {MAX_TOKENS}")
    return value


def validated_dimensions(
    value: Optional[Mapping[str, str]],
) -> Tuple[Tuple[str, str], ...]:
    """Validate bounded allocation metadata and return a stable immutable form."""

    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValidationError("dimensions must be a string-to-string mapping")
    if len(value) > MAX_DIMENSIONS:
        raise ValidationError(f"dimensions must contain at most {MAX_DIMENSIONS} entries")
    normalized: Dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = validated_text(raw_key, field="dimension key")
        dimension_value = validated_text(raw_value, field=f"dimension {key!r}")
        if key in normalized:
            raise ValidationError(f"dimension key {key!r} is duplicated after normalization")
        normalized[key] = dimension_value
    return tuple(sorted(normalized.items()))


def validated_decimal(
    value: object,
    *,
    field: str,
    maximum: Optional[Decimal] = None,
    allow_zero: bool = True,
) -> Decimal:
    """Convert a finite decimal input and enforce product bounds."""

    if isinstance(value, bool):
        raise ValidationError(f"{field} must be a decimal number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{field} must be a decimal number") from exc
    if not parsed.is_finite():
        raise ValidationError(f"{field} must be finite")
    if parsed < 0 or (not allow_zero and parsed == 0):
        qualifier = "positive" if not allow_zero else "non-negative"
        raise ValidationError(f"{field} must be {qualifier}")
    if maximum is not None and parsed > maximum:
        raise ValidationError(f"{field} must not exceed {maximum}")
    return parsed


def decimal_text(value: Decimal) -> str:
    """Serialize a decimal without exponent notation."""

    return format(value, "f")


def money(value: Decimal) -> Decimal:
    """Quantize calculated USD amounts to one trillionth of a dollar."""

    integer_digits = max(1, value.adjusted() + 1) if value else 1
    with localcontext() as context:
        context.prec = max(context.prec, integer_digits + 12)
        return value.quantize(USD_QUANTUM)


@dataclass(frozen=True)
class ModelPrice:
    """A time-versioned USD price per one million mutually exclusive tokens."""

    provider: str
    model: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    cached_input_usd_per_million: Decimal
    cache_write_input_usd_per_million: Decimal
    effective_from: datetime

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe representation."""

        return {
            "provider": self.provider,
            "model": self.model,
            "input_usd_per_million": decimal_text(self.input_usd_per_million),
            "output_usd_per_million": decimal_text(self.output_usd_per_million),
            "cached_input_usd_per_million": decimal_text(self.cached_input_usd_per_million),
            "cache_write_input_usd_per_million": decimal_text(
                self.cache_write_input_usd_per_million
            ),
            "effective_from": format_timestamp(self.effective_from),
        }


@dataclass(frozen=True)
class CostBreakdown:
    """An exact cost calculation using a snapshotted model price."""

    input_usd: Decimal
    output_usd: Decimal
    cached_input_usd: Decimal
    cache_write_input_usd: Decimal
    total_usd: Decimal
    price: ModelPrice

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe representation."""

        return {
            "input_usd": decimal_text(self.input_usd),
            "output_usd": decimal_text(self.output_usd),
            "cached_input_usd": decimal_text(self.cached_input_usd),
            "cache_write_input_usd": decimal_text(self.cache_write_input_usd),
            "total_usd": decimal_text(self.total_usd),
            "price": self.price.to_dict(),
        }


@dataclass(frozen=True)
class UsageEvent:
    """One immutable, costed model usage event."""

    event_id: str
    request_id: Optional[str]
    occurred_at: datetime
    recorded_at: datetime
    provider: str
    model: str
    project: Optional[str]
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    dimensions: Tuple[Tuple[str, str], ...]
    cost: CostBreakdown

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe representation."""

        return {
            "event_id": self.event_id,
            "request_id": self.request_id,
            "occurred_at": format_timestamp(self.occurred_at),
            "recorded_at": format_timestamp(self.recorded_at),
            "provider": self.provider,
            "model": self.model,
            "project": self.project,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "dimensions": dict(self.dimensions),
            "cost": self.cost.to_dict(),
        }


@dataclass(frozen=True)
class RecordResult:
    """The immutable event plus whether this call created it."""

    event: UsageEvent
    created: bool

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe representation."""

        return {"created": self.created, "event": self.event.to_dict()}


@dataclass(frozen=True)
class SummaryRow:
    """Aggregated usage and cost for one report group."""

    group: str
    requests: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    total_usd: Decimal

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe representation."""

        result = asdict(self)
        result["total_usd"] = decimal_text(self.total_usd)
        return result


@dataclass(frozen=True)
class BudgetStatus:
    """Evaluation of one configured spend constraint."""

    scope: str
    period: str
    period_start: datetime
    limit_usd: Decimal
    spent_usd: Decimal
    estimated_usd: Decimal
    projected_usd: Decimal
    remaining_usd: Decimal
    allowed: bool

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe representation."""

        return {
            "scope": self.scope,
            "period": self.period,
            "period_start": format_timestamp(self.period_start),
            "limit_usd": decimal_text(self.limit_usd),
            "spent_usd": decimal_text(self.spent_usd),
            "estimated_usd": decimal_text(self.estimated_usd),
            "projected_usd": decimal_text(self.projected_usd),
            "remaining_usd": decimal_text(self.remaining_usd),
            "allowed": self.allowed,
        }
