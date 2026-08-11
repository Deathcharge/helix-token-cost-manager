# Copyright 2026 Samsarix LLC
# SPDX-License-Identifier: Apache-2.0

"""Deterministic portable-ledger serialization and verification."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import BinaryIO, Dict, Iterable, List, Protocol, Sequence

from .exceptions import ValidationError
from .models import (
    DEFAULT_PRICE_PLAN,
    DEFAULT_REGION,
    DEFAULT_SERVICE_TIER,
    GLOBAL_SCOPE,
    MAX_RATE_USD_PER_MILLION,
    CostBreakdown,
    ModelPrice,
    UsageEvent,
    decimal_text,
    format_timestamp,
    money,
    parse_timestamp,
    validated_decimal,
    validated_dimensions,
    validated_input_threshold,
    validated_text,
    validated_tokens,
)

LEDGER_FORMAT = "samsarix-usage-ledger"
LEDGER_VERSION = 2
MAX_LEDGER_BYTES = 100 * 1024 * 1024
MAX_LEDGER_RECORDS = 1_000_000

CSV_FIELDS_V1 = (
    "event_id",
    "request_id",
    "occurred_at",
    "recorded_at",
    "provider",
    "model",
    "project",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "dimensions_json",
    "input_rate",
    "output_rate",
    "cached_input_rate",
    "cache_write_input_rate",
    "price_effective_from",
    "input_cost",
    "output_cost",
    "cached_input_cost",
    "cache_write_input_cost",
    "total_cost",
)

CSV_FIELDS = (
    *CSV_FIELDS_V1[:7],
    "price_plan",
    "service_tier",
    "region",
    "price_input_token_min",
    "price_input_token_max",
    *CSV_FIELDS_V1[7:],
)


class _Digest(Protocol):
    def update(self, data: bytes) -> None: ...

    def hexdigest(self) -> str: ...


@dataclass(frozen=True)
class LedgerArtifact:
    """One deterministic export plus its verification metadata."""

    content: bytes
    media_type: str
    records: int
    total_usd: Decimal
    sha256: str

    def to_dict(self) -> Dict[str, object]:
        """Return metadata suitable for CLI JSON output."""

        return {
            "format": LEDGER_FORMAT,
            "format_version": LEDGER_VERSION,
            "media_type": self.media_type,
            "records": self.records,
            "total_usd": decimal_text(self.total_usd),
            "sha256": self.sha256,
            "bytes": len(self.content),
        }


@dataclass(frozen=True)
class LedgerWriteResult:
    """Metadata for a ledger serialized directly to a binary stream."""

    media_type: str
    records: int
    total_usd: Decimal
    sha256: str
    bytes: int

    def to_dict(self) -> Dict[str, object]:
        """Return metadata suitable for CLI JSON output."""

        return {
            "format": LEDGER_FORMAT,
            "format_version": LEDGER_VERSION,
            "media_type": self.media_type,
            "records": self.records,
            "total_usd": decimal_text(self.total_usd),
            "sha256": self.sha256,
            "bytes": self.bytes,
        }


@dataclass(frozen=True)
class LedgerImportResult:
    """Result of a validated atomic ledger import or dry run."""

    records: int
    created: int
    would_create: int
    existing: int
    dry_run: bool

    def to_dict(self) -> Dict[str, object]:
        """Return a JSON-safe result."""

        return {
            "records": self.records,
            "created": self.created,
            "would_create": self.would_create,
            "existing": self.existing,
            "dry_run": self.dry_run,
        }


@dataclass(frozen=True)
class ReconciliationResult:
    """Comparison of local immutable usage cost with a provider invoice total."""

    provider: str
    invoice_id: str | None
    billing_period_start: str
    billing_period_end: str
    currency: str
    events: int
    local_total: Decimal
    billed_total: Decimal
    variance: Decimal
    tolerance: Decimal
    reconciled: bool

    def to_dict(self) -> Dict[str, object]:
        """Return a JSON-safe reconciliation record."""

        return {
            "provider": self.provider,
            "invoice_id": self.invoice_id,
            "billing_period_start": self.billing_period_start,
            "billing_period_end": self.billing_period_end,
            "currency": self.currency,
            "events": self.events,
            "local_total": decimal_text(self.local_total),
            "billed_total": decimal_text(self.billed_total),
            "variance": decimal_text(self.variance),
            "tolerance": decimal_text(self.tolerance),
            "reconciled": self.reconciled,
        }


def reconcile_invoice(
    events: Iterable[UsageEvent],
    *,
    provider: str,
    billing_period_start: object,
    billing_period_end: object,
    billed_total: object,
    invoice_id: object = None,
    tolerance: object = "0.01",
) -> ReconciliationResult:
    """Compare one provider-period invoice total with local snapshotted usage cost."""

    normalized_provider = validated_text(provider, field="provider")
    start = parse_timestamp(billing_period_start, field="billing_period_start")
    end = parse_timestamp(billing_period_end, field="billing_period_end")
    if start >= end:
        raise ValidationError("billing_period_start must be earlier than billing_period_end")
    normalized_billed = validated_decimal(billed_total, field="billed_total")
    normalized_tolerance = validated_decimal(tolerance, field="tolerance")
    normalized_invoice = (
        validated_text(invoice_id, field="invoice_id") if invoice_id is not None else None
    )
    matching = [
        event
        for event in events
        if event.provider == normalized_provider and start <= event.occurred_at < end
    ]
    local_total = money(sum((event.cost.total_usd for event in matching), Decimal("0")))
    variance = money(normalized_billed - local_total)
    return ReconciliationResult(
        provider=normalized_provider,
        invoice_id=normalized_invoice,
        billing_period_start=format_timestamp(start),
        billing_period_end=format_timestamp(end),
        currency="USD",
        events=len(matching),
        local_total=local_total,
        billed_total=normalized_billed,
        variance=variance,
        tolerance=normalized_tolerance,
        reconciled=abs(variance) <= normalized_tolerance,
    )


def event_record(event: UsageEvent) -> Dict[str, object]:
    """Flatten an immutable event without losing exact accounting values."""

    price = event.cost.price
    return {
        "event_id": event.event_id,
        "request_id": event.request_id,
        "occurred_at": format_timestamp(event.occurred_at),
        "recorded_at": format_timestamp(event.recorded_at),
        "provider": event.provider,
        "model": event.model,
        "project": event.project,
        "price_plan": price.price_plan,
        "service_tier": price.service_tier,
        "region": price.region,
        "price_input_token_min": price.input_token_min,
        "price_input_token_max": price.input_token_max,
        "input_tokens": event.input_tokens,
        "output_tokens": event.output_tokens,
        "cached_input_tokens": event.cached_input_tokens,
        "cache_write_input_tokens": event.cache_write_input_tokens,
        "dimensions": dict(event.dimensions),
        "input_rate": decimal_text(price.input_usd_per_million),
        "output_rate": decimal_text(price.output_usd_per_million),
        "cached_input_rate": decimal_text(price.cached_input_usd_per_million),
        "cache_write_input_rate": decimal_text(price.cache_write_input_usd_per_million),
        "price_effective_from": price.to_dict()["effective_from"],
        "input_cost": decimal_text(event.cost.input_usd),
        "output_cost": decimal_text(event.cost.output_usd),
        "cached_input_cost": decimal_text(event.cost.cached_input_usd),
        "cache_write_input_cost": decimal_text(event.cost.cache_write_input_usd),
        "total_cost": decimal_text(event.cost.total_usd),
    }


def _ordered(events: Iterable[UsageEvent]) -> List[UsageEvent]:
    ordered = sorted(events, key=lambda event: (event.occurred_at, event.event_id))
    if len(ordered) > MAX_LEDGER_RECORDS:
        raise ValidationError(f"ledger must not exceed {MAX_LEDGER_RECORDS} records")
    return ordered


def _artifact(content: bytes, *, media_type: str, events: Sequence[UsageEvent]) -> LedgerArtifact:
    if len(content) > MAX_LEDGER_BYTES:
        raise ValidationError(f"ledger must not exceed {MAX_LEDGER_BYTES} bytes")
    return LedgerArtifact(
        content=content,
        media_type=media_type,
        records=len(events),
        total_usd=sum((event.cost.total_usd for event in events), Decimal("0")),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _write_chunk(destination: BinaryIO, chunk: bytes, digest: _Digest, current_bytes: int) -> int:
    new_size = current_bytes + len(chunk)
    if new_size > MAX_LEDGER_BYTES:
        raise ValidationError(f"ledger must not exceed {MAX_LEDGER_BYTES} bytes")
    destination.write(chunk)
    digest.update(chunk)
    return new_size


def write_jsonl(destination: BinaryIO, events: Iterable[UsageEvent]) -> LedgerWriteResult:
    """Incrementally serialize already-ordered events as canonical JSON Lines."""

    digest = hashlib.sha256()
    byte_count = 0
    record_count = 0
    total = Decimal("0")
    header = {"format": LEDGER_FORMAT, "format_version": LEDGER_VERSION, "type": "manifest"}
    chunk = (json.dumps(header, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    byte_count = _write_chunk(destination, chunk, digest, byte_count)
    for event in events:
        if record_count >= MAX_LEDGER_RECORDS:
            raise ValidationError(f"ledger must not exceed {MAX_LEDGER_RECORDS} records")
        line = json.dumps(
            {"record": event_record(event), "type": "usage_event"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        byte_count = _write_chunk(destination, (line + "\n").encode("utf-8"), digest, byte_count)
        record_count += 1
        total += event.cost.total_usd
    return LedgerWriteResult(
        media_type="application/x-ndjson",
        records=record_count,
        total_usd=total,
        sha256=digest.hexdigest(),
        bytes=byte_count,
    )


def _csv_chunk(*, header: bool = False, record: Dict[str, object] | None = None) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\r\n")
    if header:
        writer.writeheader()
    elif record is not None:
        writer.writerow(record)
    return output.getvalue().encode("utf-8")


def write_csv(destination: BinaryIO, events: Iterable[UsageEvent]) -> LedgerWriteResult:
    """Incrementally serialize already-ordered events as RFC 4180 CSV."""

    digest = hashlib.sha256()
    byte_count = _write_chunk(destination, _csv_chunk(header=True), digest, 0)
    record_count = 0
    total = Decimal("0")
    for event in events:
        if record_count >= MAX_LEDGER_RECORDS:
            raise ValidationError(f"ledger must not exceed {MAX_LEDGER_RECORDS} records")
        record = event_record(event)
        dimensions = record.pop("dimensions")
        record["dimensions_json"] = json.dumps(
            dimensions, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        byte_count = _write_chunk(destination, _csv_chunk(record=record), digest, byte_count)
        record_count += 1
        total += event.cost.total_usd
    return LedgerWriteResult(
        media_type="text/csv",
        records=record_count,
        total_usd=total,
        sha256=digest.hexdigest(),
        bytes=byte_count,
    )


def export_jsonl(events: Iterable[UsageEvent]) -> LedgerArtifact:
    """Serialize events as canonical UTF-8 JSON Lines."""

    ordered = _ordered(events)
    output = io.BytesIO()
    write_jsonl(output, ordered)
    content = output.getvalue()
    return _artifact(content, media_type="application/x-ndjson", events=ordered)


def export_csv(events: Iterable[UsageEvent]) -> LedgerArtifact:
    """Serialize events as RFC 4180-compatible UTF-8 CSV."""

    ordered = _ordered(events)
    output = io.BytesIO()
    write_csv(output, ordered)
    content = output.getvalue()
    return _artifact(content, media_type="text/csv", events=ordered)


def verify_digest(content: bytes, expected_sha256: str) -> str:
    """Verify a caller-supplied SHA-256 digest using constant-time comparison."""

    normalized = expected_sha256.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValidationError(
            "sha256 must be exactly 64 lowercase or uppercase hexadecimal characters"
        )
    actual = hashlib.sha256(content).hexdigest()
    if not hmac.compare_digest(actual, normalized):
        raise ValidationError(f"ledger sha256 mismatch: expected {normalized}, calculated {actual}")
    return actual


def _required(record: Mapping[str, object], field: str) -> object:
    if field not in record:
        raise ValidationError(f"ledger record is missing {field!r}")
    return record[field]


def record_event(record: Mapping[str, object]) -> UsageEvent:
    """Validate one flat portable record and reconstruct its immutable event."""

    provider = validated_text(_required(record, "provider"), field="provider")
    model = validated_text(_required(record, "model"), field="model")
    request_value = record.get("request_id")
    project_value = record.get("project")
    request_id = (
        validated_text(request_value, field="request_id")
        if request_value not in (None, "")
        else None
    )
    project = (
        validated_text(project_value, field="project", reserved=GLOBAL_SCOPE)
        if project_value not in (None, "")
        else None
    )
    dimensions_value = record.get("dimensions", {})
    if not isinstance(dimensions_value, Mapping):
        raise ValidationError("dimensions must be a JSON object")
    dimensions = validated_dimensions(dimensions_value)
    tokens = {
        name: validated_tokens(_required(record, name), field=name)
        for name in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
        )
    }
    price_plan = validated_text(record.get("price_plan", DEFAULT_PRICE_PLAN), field="price_plan")
    service_tier = validated_text(
        record.get("service_tier", DEFAULT_SERVICE_TIER), field="service_tier"
    )
    region = validated_text(record.get("region", DEFAULT_REGION), field="region")
    price_input_token_min = validated_input_threshold(
        record.get("price_input_token_min", 0), field="price_input_token_min"
    )
    maximum_value = record.get("price_input_token_max")
    price_input_token_max = (
        None
        if maximum_value in (None, "")
        else validated_input_threshold(maximum_value, field="price_input_token_max")
    )
    if price_input_token_max is not None and price_input_token_max < price_input_token_min:
        raise ValidationError(
            "price_input_token_max must be greater than or equal to price_input_token_min"
        )
    rated_input_tokens = (
        tokens["input_tokens"] + tokens["cached_input_tokens"] + tokens["cache_write_input_tokens"]
    )
    if rated_input_tokens < price_input_token_min or (
        price_input_token_max is not None and rated_input_tokens > price_input_token_max
    ):
        raise ValidationError("snapshotted price input-token range does not cover usage")
    rates = {
        name: validated_decimal(
            _required(record, name), field=name, maximum=MAX_RATE_USD_PER_MILLION
        )
        for name in (
            "input_rate",
            "output_rate",
            "cached_input_rate",
            "cache_write_input_rate",
        )
    }
    costs = {
        name: validated_decimal(_required(record, name), field=name)
        for name in (
            "input_cost",
            "output_cost",
            "cached_input_cost",
            "cache_write_input_cost",
            "total_cost",
        )
    }
    expected_costs = {
        "input_cost": money(Decimal(tokens["input_tokens"]) * rates["input_rate"] / 1_000_000),
        "output_cost": money(Decimal(tokens["output_tokens"]) * rates["output_rate"] / 1_000_000),
        "cached_input_cost": money(
            Decimal(tokens["cached_input_tokens"]) * rates["cached_input_rate"] / 1_000_000
        ),
        "cache_write_input_cost": money(
            Decimal(tokens["cache_write_input_tokens"])
            * rates["cache_write_input_rate"]
            / 1_000_000
        ),
    }
    for name, expected in expected_costs.items():
        if costs[name] != expected:
            raise ValidationError(f"{name} does not match the snapshotted tokens and rate")
    expected_total = money(sum(expected_costs.values(), Decimal("0")))
    if costs["total_cost"] != expected_total:
        raise ValidationError("total_cost does not match component costs")
    price = ModelPrice(
        provider=provider,
        model=model,
        input_usd_per_million=rates["input_rate"],
        output_usd_per_million=rates["output_rate"],
        cached_input_usd_per_million=rates["cached_input_rate"],
        cache_write_input_usd_per_million=rates["cache_write_input_rate"],
        effective_from=parse_timestamp(
            _required(record, "price_effective_from"),
            field="price_effective_from",
        ),
        price_plan=price_plan,
        service_tier=service_tier,
        region=region,
        input_token_min=price_input_token_min,
        input_token_max=price_input_token_max,
    )
    return UsageEvent(
        event_id=validated_text(_required(record, "event_id"), field="event_id"),
        request_id=request_id,
        occurred_at=parse_timestamp(_required(record, "occurred_at"), field="occurred_at"),
        recorded_at=parse_timestamp(_required(record, "recorded_at"), field="recorded_at"),
        provider=provider,
        model=model,
        project=project,
        input_tokens=tokens["input_tokens"],
        output_tokens=tokens["output_tokens"],
        cached_input_tokens=tokens["cached_input_tokens"],
        cache_write_input_tokens=tokens["cache_write_input_tokens"],
        dimensions=dimensions,
        cost=CostBreakdown(
            input_usd=costs["input_cost"],
            output_usd=costs["output_cost"],
            cached_input_usd=costs["cached_input_cost"],
            cache_write_input_usd=costs["cache_write_input_cost"],
            total_usd=costs["total_cost"],
            price=price,
        ),
    )


def import_jsonl(content: bytes, *, expected_sha256: str | None = None) -> List[UsageEvent]:
    """Parse and fully validate a bounded canonical-ledger JSONL artifact."""

    if len(content) > MAX_LEDGER_BYTES:
        raise ValidationError(f"ledger must not exceed {MAX_LEDGER_BYTES} bytes")
    if expected_sha256 is not None:
        verify_digest(content, expected_sha256)
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValidationError("ledger must be UTF-8") from exc
    if not lines:
        raise ValidationError("ledger must contain a manifest")
    try:
        manifest = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValidationError("ledger manifest must be valid JSON") from exc
    supported_manifests = (
        {"format": LEDGER_FORMAT, "format_version": 1, "type": "manifest"},
        {"format": LEDGER_FORMAT, "format_version": LEDGER_VERSION, "type": "manifest"},
    )
    if manifest not in supported_manifests:
        raise ValidationError("unsupported ledger manifest or format version")
    legacy = manifest["format_version"] == 1
    if len(lines) - 1 > MAX_LEDGER_RECORDS:
        raise ValidationError(f"ledger must not exceed {MAX_LEDGER_RECORDS} records")
    events: List[UsageEvent] = []
    for line_number, line in enumerate(lines[1:], start=2):
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"ledger line {line_number} must be valid JSON") from exc
        if not isinstance(envelope, dict) or envelope.get("type") != "usage_event":
            raise ValidationError(f"ledger line {line_number} must be a usage_event envelope")
        record = envelope.get("record")
        if not isinstance(record, dict):
            raise ValidationError(f"ledger line {line_number} record must be an object")
        try:
            if not legacy:
                for field in (
                    "price_plan",
                    "service_tier",
                    "region",
                    "price_input_token_min",
                    "price_input_token_max",
                ):
                    _required(record, field)
            events.append(record_event(record))
        except ValidationError as exc:
            raise ValidationError(f"ledger line {line_number}: {exc}") from exc
    _validate_unique(events)
    return events


def import_csv(content: bytes, *, expected_sha256: str | None = None) -> List[UsageEvent]:
    """Parse and fully validate a bounded RFC 4180 ledger artifact."""

    if len(content) > MAX_LEDGER_BYTES:
        raise ValidationError(f"ledger must not exceed {MAX_LEDGER_BYTES} bytes")
    if expected_sha256 is not None:
        verify_digest(content, expected_sha256)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("ledger must be UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fieldnames = tuple(reader.fieldnames or ())
    if fieldnames not in (CSV_FIELDS_V1, CSV_FIELDS):
        raise ValidationError("CSV ledger header does not match format version 1 or 2")
    legacy = fieldnames == CSV_FIELDS_V1
    events: List[UsageEvent] = []
    for row_number, row in enumerate(reader, start=2):
        if len(events) >= MAX_LEDGER_RECORDS:
            raise ValidationError(f"ledger must not exceed {MAX_LEDGER_RECORDS} records")
        if None in row:
            raise ValidationError(f"CSV ledger row {row_number} has extra columns")
        try:
            dimensions = json.loads(row.pop("dimensions_json") or "{}")
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"CSV ledger row {row_number} dimensions_json must be valid JSON"
            ) from exc
        record: Dict[str, object] = dict(row)
        record["dimensions"] = dimensions
        if legacy:
            record.update(
                {
                    "price_plan": DEFAULT_PRICE_PLAN,
                    "service_tier": DEFAULT_SERVICE_TIER,
                    "region": DEFAULT_REGION,
                    "price_input_token_min": 0,
                    "price_input_token_max": None,
                }
            )
        for field in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
        ):
            value = record[field]
            if not isinstance(value, str) or not value.isascii() or not value.isdigit():
                raise ValidationError(f"CSV ledger row {row_number} {field} must be an integer")
            record[field] = int(value)
        if not legacy:
            for field in ("price_input_token_min", "price_input_token_max"):
                value = record[field]
                if field == "price_input_token_max" and value == "":
                    record[field] = None
                elif not isinstance(value, str) or not value.isascii() or not value.isdigit():
                    raise ValidationError(f"CSV ledger row {row_number} {field} must be an integer")
                else:
                    record[field] = int(value)
        try:
            events.append(record_event(record))
        except ValidationError as exc:
            raise ValidationError(f"CSV ledger row {row_number}: {exc}") from exc
    _validate_unique(events)
    return events


def _validate_unique(events: Sequence[UsageEvent]) -> None:
    event_ids: set[str] = set()
    request_ids: set[str] = set()
    for event in events:
        if event.event_id in event_ids:
            raise ValidationError(f"ledger repeats event_id {event.event_id!r}")
        event_ids.add(event.event_id)
        if event.request_id is not None:
            if event.request_id in request_ids:
                raise ValidationError(f"ledger repeats request_id {event.request_id!r}")
            request_ids.add(event.request_id)
