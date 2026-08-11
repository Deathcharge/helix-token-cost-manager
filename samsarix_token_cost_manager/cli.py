# Copyright 2026 Samsarix LLC
# SPDX-License-Identifier: Apache-2.0

"""Command-line interface for local token cost accounting."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, List, Optional, Sequence, Tuple, cast

from . import __version__
from .adapters import from_anthropic_response, from_openai_response, from_otel_attributes
from .exceptions import CostManagerError, ValidationError
from .ledger import (
    MAX_LEDGER_BYTES,
    LedgerWriteResult,
    import_csv,
    import_jsonl,
    reconcile_invoice,
    write_csv,
    write_jsonl,
)
from .manager import SCHEMA_VERSION, VALID_GROUPS, VALID_PERIODS, CostManager, default_database_path
from .models import (
    BudgetStatus,
    RecordResult,
    SummaryRow,
    decimal_text,
    validated_decimal,
)

EXIT_ERROR = 2
EXIT_BUDGET_EXCEEDED = 3
EXIT_RECONCILIATION_VARIANCE = 4
MAX_INGEST_BYTES = 10 * 1024 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="samsarix-cost",
        description=("Track provider-reported LLM token usage and calculate exact local USD cost."),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help=("SQLite database path (default: SAMSARIX_COST_DB or the platform data directory)"),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit stable JSON for automation",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="initialize the local database")

    price = subparsers.add_parser("price", help="manage explicit model prices")
    price_commands = price.add_subparsers(dest="price_command", required=True)
    price_set = price_commands.add_parser("set", help="add or replace a price version")
    price_set.add_argument("--provider", required=True)
    price_set.add_argument("--model", required=True)
    price_set.add_argument(
        "--input",
        required=True,
        dest="input_rate",
        help="USD per one million non-cached input tokens",
    )
    price_set.add_argument(
        "--cache-write-input",
        dest="cache_write_input_rate",
        help="USD per one million cache-creation tokens (defaults to --input)",
    )
    price_set.add_argument(
        "--output",
        required=True,
        dest="output_rate",
        help="USD per one million output tokens",
    )
    price_set.add_argument(
        "--cached-input",
        dest="cached_input_rate",
        help="USD per one million cached input tokens (defaults to --input)",
    )
    price_set.add_argument(
        "--effective-from",
        help="ISO-8601 date/timestamp (defaults to now)",
    )
    _add_price_selector_arguments(price_set, include_thresholds=True)
    price_commands.add_parser("list", help="list configured price versions")

    estimate = subparsers.add_parser("estimate", help="calculate cost without recording usage")
    _add_usage_arguments(estimate, include_identity=False)

    record = subparsers.add_parser("record", help="record one costed usage event")
    _add_usage_arguments(record, include_identity=True)

    ingest = subparsers.add_parser(
        "ingest", help="record usage from an OpenAI, Anthropic, or OpenTelemetry JSON payload"
    )
    ingest.add_argument("--format", choices=("openai", "anthropic", "otel"), required=True)
    ingest.add_argument("--file", required=True, help="JSON file path, or - for standard input")
    ingest.add_argument("--project", help="optional cost allocation project")
    ingest.add_argument("--dimension", action="append", default=[], metavar="KEY=VALUE")
    ingest.add_argument("--occurred-at", help="ISO-8601 timestamp (defaults to now)")
    _add_price_selector_arguments(ingest, include_thresholds=False)

    report = subparsers.add_parser("report", help="summarize recorded usage and cost")
    report.add_argument("--since", help="inclusive ISO-8601 date/timestamp")
    report.add_argument("--until", help="exclusive ISO-8601 date/timestamp")
    report.add_argument("--month", help="UTC month in YYYY-MM form")
    report.add_argument("--project", help="limit the report to one project")
    report.add_argument(
        "--dimension",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="require an exact allocation dimension; repeat for AND matching",
    )
    report.add_argument(
        "--group-by",
        default="none",
        help=(f"aggregation dimension ({', '.join(VALID_GROUPS)} or dimension:KEY; default: none)"),
    )

    ledger = subparsers.add_parser("ledger", help="export or verify portable usage ledgers")
    ledger_commands = ledger.add_subparsers(dest="ledger_command", required=True)
    ledger_export = ledger_commands.add_parser(
        "export", help="atomically export deterministic JSONL or CSV"
    )
    ledger_export.add_argument("--format", choices=("jsonl", "csv"), required=True)
    ledger_export.add_argument("--file", type=Path, required=True)
    ledger_export.add_argument("--since", help="inclusive ISO-8601 date/timestamp")
    ledger_export.add_argument("--until", help="exclusive ISO-8601 date/timestamp")
    ledger_export.add_argument("--project", help="limit export to one project")
    ledger_export.add_argument("--force", action="store_true", help="replace an existing file")
    ledger_import = ledger_commands.add_parser(
        "import", help="validate and atomically import a JSONL or CSV usage ledger"
    )
    ledger_import.add_argument("--format", choices=("jsonl", "csv"), required=True)
    ledger_import.add_argument("--file", type=Path, required=True)
    ledger_import.add_argument("--sha256", help="required artifact digest for verification")
    ledger_import.add_argument("--dry-run", action="store_true", help="validate without writing")
    ledger_import.add_argument(
        "--errors-file", type=Path, help="atomically write a JSON error ledger on rejection"
    )
    reconcile = ledger_commands.add_parser(
        "reconcile", help="compare local usage cost with one provider invoice total"
    )
    reconcile.add_argument("--provider", required=True)
    reconcile.add_argument("--period-start", required=True)
    reconcile.add_argument("--period-end", required=True)
    reconcile.add_argument("--billed-usd", required=True)
    reconcile.add_argument("--invoice-id")
    reconcile.add_argument("--tolerance", default="0.01", help="allowed absolute USD variance")

    budget = subparsers.add_parser("budget", help="configure and check spend budgets")
    budget_commands = budget.add_subparsers(dest="budget_command", required=True)
    budget_set = budget_commands.add_parser("set", help="set a global/project budget")
    budget_set.add_argument("--amount", required=True, help="positive USD limit")
    budget_set.add_argument("--period", choices=VALID_PERIODS, required=True)
    budget_set.add_argument("--project", help="omit for a global budget")

    budget_check = budget_commands.add_parser(
        "check", help="check whether estimated spend fits all applicable budgets"
    )
    budget_check.add_argument("--amount", help="already-calculated estimated USD cost")
    budget_check.add_argument("--provider", help="provider for a token-based estimate")
    budget_check.add_argument("--model", help="model for a token-based estimate")
    budget_check.add_argument("--input-tokens", type=int, default=0)
    budget_check.add_argument("--output-tokens", type=int, default=0)
    budget_check.add_argument("--cached-input-tokens", type=int, default=0)
    budget_check.add_argument("--cache-write-input-tokens", type=int, default=0)
    budget_check.add_argument("--project", help="apply project and global budgets")
    budget_check.add_argument("--at", help="ISO-8601 timestamp (defaults to now)")
    _add_price_selector_arguments(budget_check, include_thresholds=False)
    return parser


def _add_price_selector_arguments(
    parser: argparse.ArgumentParser, *, include_thresholds: bool
) -> None:
    parser.add_argument("--price-plan", default="list", help="price book or contract name")
    parser.add_argument("--service-tier", default="standard", help="processing/service tier")
    parser.add_argument("--region", default="global", help="billing or inference geography")
    if include_thresholds:
        parser.add_argument(
            "--input-token-min",
            type=int,
            default=0,
            help="inclusive total input-token threshold for this rule",
        )
        parser.add_argument(
            "--input-token-max",
            type=int,
            help="inclusive upper threshold; omit for no upper bound",
        )


def _add_usage_arguments(parser: argparse.ArgumentParser, *, include_identity: bool) -> None:
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--input-tokens", type=int, default=0)
    parser.add_argument("--output-tokens", type=int, default=0)
    parser.add_argument("--cached-input-tokens", type=int, default=0)
    parser.add_argument("--cache-write-input-tokens", type=int, default=0)
    _add_price_selector_arguments(parser, include_thresholds=False)
    if include_identity:
        parser.add_argument(
            "--request-id",
            help="idempotency key; retries with the same usage return the original event",
        )
        parser.add_argument("--project", help="optional non-sensitive cost allocation label")
        parser.add_argument("--dimension", action="append", default=[], metavar="KEY=VALUE")
        parser.add_argument("--occurred-at", help="ISO-8601 timestamp (defaults to now)")
    else:
        parser.add_argument("--at", help="price lookup timestamp (defaults to now)")


def _month_bounds(month: str) -> Tuple[str, Optional[str]]:
    try:
        start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValidationError("month must use YYYY-MM") from exc
    if start.month == 12:
        try:
            end = start.replace(year=start.year + 1, month=1)
        except ValueError:
            return start.isoformat(), None
    else:
        end = start.replace(month=start.month + 1)
    return start.isoformat(), end.isoformat()


def _parse_dimensions(values: Sequence[str]) -> Dict[str, str]:
    dimensions: Dict[str, str] = {}
    for item in values:
        key, separator, value = item.partition("=")
        if not separator or not key.strip() or not value.strip():
            raise ValidationError("dimension must use non-empty KEY=VALUE syntax")
        normalized_key = key.strip()
        if normalized_key in dimensions:
            raise ValidationError(f"dimension {normalized_key!r} was provided more than once")
        dimensions[normalized_key] = value.strip()
    return dimensions


def _load_json_payload(path: str) -> Mapping[str, object]:
    try:
        if path == "-":
            content = sys.stdin.read(MAX_INGEST_BYTES + 1)
        else:
            payload_path = Path(path)
            if payload_path.stat().st_size > MAX_INGEST_BYTES:
                raise ValidationError(f"ingest payload must not exceed {MAX_INGEST_BYTES} bytes")
            content = payload_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValidationError(f"could not read ingest payload: {exc}") from exc
    if len(content.encode("utf-8")) > MAX_INGEST_BYTES:
        raise ValidationError(f"ingest payload must not exceed {MAX_INGEST_BYTES} bytes")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValidationError("ingest payload must be a UTF-8 JSON object") from exc
    if not isinstance(payload, dict):
        raise ValidationError("ingest payload must be a JSON object")
    return payload


def _read_bounded_bytes(path: Path, *, maximum: int, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValidationError(f"could not open {label}: {exc}") from exc
    try:
        try:
            details = os.fstat(descriptor)
        except OSError as exc:
            raise ValidationError(f"could not stat {label}: {exc}") from exc
        if not stat.S_ISREG(details.st_mode):
            raise ValidationError(f"{label} must be a regular file")
        if details.st_size > maximum:
            raise ValidationError(f"{label} must not exceed {maximum} bytes")
        chunks: List[bytes] = []
        total = 0
        while total <= maximum:
            try:
                chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - total))
            except OSError as exc:
                raise ValidationError(f"could not read {label}: {exc}") from exc
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > maximum:
            raise ValidationError(f"{label} must not exceed {maximum} bytes")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _emit_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def _write_artifact(path: Path, content: bytes, *, force: bool) -> None:
    """Write a complete artifact atomically without leaving partial output."""

    destination = path.resolve()
    if destination.exists() and not force:
        raise ValidationError(f"output file already exists: {destination}; use --force to replace")
    if not destination.parent.is_dir():
        raise ValidationError(f"output directory does not exist: {destination.parent}")
    temporary_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        if destination.exists() and not force:
            raise ValidationError(
                f"output file appeared during export: {destination}; use --force to replace"
            )
        os.replace(temporary_name, destination)
        temporary_name = None
    finally:
        if temporary_name is not None:
            with suppress(FileNotFoundError):
                Path(temporary_name).unlink()


def _write_streamed_artifact(
    path: Path,
    writer: Callable[[BinaryIO], LedgerWriteResult],
    *,
    force: bool,
) -> LedgerWriteResult:
    """Stream and atomically publish an artifact without retaining its bytes."""

    destination = path.resolve()
    if destination.exists() and not force:
        raise ValidationError(f"output file already exists: {destination}; use --force to replace")
    if not destination.parent.is_dir():
        raise ValidationError(f"output directory does not exist: {destination.parent}")
    temporary_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            result = writer(cast(BinaryIO, temporary))
            temporary.flush()
            os.fsync(temporary.fileno())
        if destination.exists() and not force:
            raise ValidationError(
                f"output file appeared during export: {destination}; use --force to replace"
            )
        os.replace(temporary_name, destination)
        temporary_name = None
        return result
    finally:
        if temporary_name is not None:
            with suppress(FileNotFoundError):
                Path(temporary_name).unlink()


def _format_usd(value: Decimal) -> str:
    return f"${value:.12f}".rstrip("0").rstrip(".")


def _render_prices(prices: Sequence[Any]) -> None:
    if not prices:
        print("No model prices configured. Add one with `samsarix-cost price set`.")
        return
    headers = (
        "PROVIDER",
        "MODEL",
        "PLAN",
        "TIER",
        "REGION",
        "INPUT RANGE",
        "INPUT/1M",
        "OUTPUT/1M",
        "CACHE READ/1M",
        "CACHE WRITE/1M",
        "EFFECTIVE",
    )
    rows = [
        (
            price.provider,
            price.model,
            price.price_plan,
            price.service_tier,
            price.region,
            (
                f"{price.input_token_min}.."
                if price.input_token_max is None
                else f"{price.input_token_min}..{price.input_token_max}"
            ),
            _format_usd(price.input_usd_per_million),
            _format_usd(price.output_usd_per_million),
            _format_usd(price.cached_input_usd_per_million),
            _format_usd(price.cache_write_input_usd_per_million),
            price.to_dict()["effective_from"],
        )
        for price in prices
    ]
    _table(headers, rows)


def _render_report(rows: Sequence[SummaryRow]) -> None:
    if not rows:
        print("No usage events matched the report filters.")
        return
    table_rows = [
        (
            row.group,
            str(row.requests),
            str(row.input_tokens),
            str(row.output_tokens),
            str(row.cached_input_tokens),
            str(row.cache_write_input_tokens),
            _format_usd(row.total_usd),
        )
        for row in rows
    ]
    _table(
        (
            "GROUP",
            "REQUESTS",
            "INPUT",
            "OUTPUT",
            "CACHE READ",
            "CACHE WRITE",
            "TOTAL USD",
        ),
        table_rows,
    )


def _render_budgets(statuses: Sequence[BudgetStatus]) -> None:
    if not statuses:
        print("No applicable budgets configured; request is allowed.")
        return
    rows = [
        (
            status.scope,
            status.period,
            _format_usd(status.spent_usd),
            _format_usd(status.estimated_usd),
            _format_usd(status.limit_usd),
            "ALLOW" if status.allowed else "DENY",
        )
        for status in statuses
    ]
    _table(("SCOPE", "PERIOD", "SPENT", "ESTIMATE", "LIMIT", "DECISION"), rows)


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    pattern = "  ".join(f"{{:<{width}}}" for width in widths)
    print(pattern.format(*headers))
    print(pattern.format(*("-" * width for width in widths)))
    for row in rows:
        print(pattern.format(*row))


def _render_record_result(
    manager: CostManager,
    record_result: RecordResult,
    *,
    emit_json: bool,
    source_format: Optional[str] = None,
) -> None:
    statuses = manager.check_budgets(
        estimated_usd=Decimal("0"),
        project=record_result.event.project,
        at=record_result.event.occurred_at,
    )
    payload = record_result.to_dict()
    if source_format is not None:
        payload["source_format"] = source_format
    payload["budget_statuses"] = [status.to_dict() for status in statuses]
    if emit_json:
        _emit_json(payload)
        return
    verb = "Recorded" if record_result.created else "Already recorded"
    origin = f" from {source_format}" if source_format is not None else ""
    print(
        f"{verb} {record_result.event.event_id}{origin}: "
        f"{_format_usd(record_result.event.cost.total_usd)}"
    )
    if any(not status.allowed for status in statuses):
        print(
            "Warning: recorded spend exceeds an applicable budget; "
            "use `budget check` before future calls.",
            file=sys.stderr,
        )


def _dispatch(arguments: argparse.Namespace) -> int:
    database = arguments.db or default_database_path()
    with CostManager(database) as manager:
        if arguments.command == "init":
            payload = {
                "database": str(manager.database.resolve()),
                "schema_version": SCHEMA_VERSION,
            }
            if arguments.json:
                _emit_json(payload)
            else:
                print(f"Initialized {payload['database']} (schema {SCHEMA_VERSION}).")
            return 0

        if arguments.command == "price" and arguments.price_command == "set":
            price = manager.set_price(
                provider=arguments.provider,
                model=arguments.model,
                input_usd_per_million=arguments.input_rate,
                output_usd_per_million=arguments.output_rate,
                cached_input_usd_per_million=arguments.cached_input_rate,
                cache_write_input_usd_per_million=arguments.cache_write_input_rate,
                effective_from=arguments.effective_from,
                price_plan=arguments.price_plan,
                service_tier=arguments.service_tier,
                region=arguments.region,
                input_token_min=arguments.input_token_min,
                input_token_max=arguments.input_token_max,
            )
            if arguments.json:
                _emit_json(price.to_dict())
            else:
                print(
                    f"Saved {price.provider}/{price.model} pricing effective "
                    f"{price.to_dict()['effective_from']}."
                )
            return 0

        if arguments.command == "price" and arguments.price_command == "list":
            prices = manager.list_prices()
            if arguments.json:
                _emit_json([price.to_dict() for price in prices])
            else:
                _render_prices(prices)
            return 0

        if arguments.command == "estimate":
            estimate_result = manager.estimate(
                provider=arguments.provider,
                model=arguments.model,
                input_tokens=arguments.input_tokens,
                output_tokens=arguments.output_tokens,
                cached_input_tokens=arguments.cached_input_tokens,
                cache_write_input_tokens=arguments.cache_write_input_tokens,
                at=arguments.at,
                price_plan=arguments.price_plan,
                service_tier=arguments.service_tier,
                region=arguments.region,
            )
            if arguments.json:
                _emit_json(estimate_result.to_dict())
            else:
                print(f"Estimated cost: {_format_usd(estimate_result.total_usd)}")
            return 0

        if arguments.command == "record":
            record_result = manager.record(
                provider=arguments.provider,
                model=arguments.model,
                input_tokens=arguments.input_tokens,
                output_tokens=arguments.output_tokens,
                cached_input_tokens=arguments.cached_input_tokens,
                cache_write_input_tokens=arguments.cache_write_input_tokens,
                request_id=arguments.request_id,
                project=arguments.project,
                dimensions=_parse_dimensions(arguments.dimension),
                occurred_at=arguments.occurred_at,
                price_plan=arguments.price_plan,
                service_tier=arguments.service_tier,
                region=arguments.region,
            )
            _render_record_result(manager, record_result, emit_json=arguments.json)
            return 0

        if arguments.command == "ingest":
            ingest_payload = _load_json_payload(arguments.file)
            adapters = {
                "openai": from_openai_response,
                "anthropic": from_anthropic_response,
                "otel": from_otel_attributes,
            }
            measurement = adapters[arguments.format](ingest_payload)
            record_result = manager.record_measurement(
                measurement,
                project=arguments.project,
                dimensions=_parse_dimensions(arguments.dimension),
                occurred_at=arguments.occurred_at,
                price_plan=arguments.price_plan,
                service_tier=arguments.service_tier,
                region=arguments.region,
            )
            _render_record_result(
                manager,
                record_result,
                emit_json=arguments.json,
                source_format=arguments.format,
            )
            return 0

        if arguments.command == "report":
            since = arguments.since
            until = arguments.until
            if arguments.month:
                if since or until:
                    raise ValidationError("month cannot be combined with since or until")
                since, until = _month_bounds(arguments.month)
            rows = manager.summarize(
                since=since,
                until=until,
                project=arguments.project,
                group_by=arguments.group_by,
                dimensions=_parse_dimensions(arguments.dimension),
            )
            if arguments.json:
                _emit_json([row.to_dict() for row in rows])
            else:
                _render_report(rows)
            return 0

        if arguments.command == "ledger" and arguments.ledger_command == "export":
            export_events = manager.iter_events(
                since=arguments.since,
                until=arguments.until,
                project=arguments.project,
            )
            writers = {"jsonl": write_jsonl, "csv": write_csv}
            artifact = _write_streamed_artifact(
                arguments.file,
                lambda destination: writers[arguments.format](destination, export_events),
                force=arguments.force,
            )
            payload = artifact.to_dict()
            payload["path"] = str(arguments.file.resolve())
            if arguments.json:
                _emit_json(payload)
            else:
                print(
                    f"Exported {artifact.records} events to {payload['path']} "
                    f"(sha256 {artifact.sha256})."
                )
            return 0

        if arguments.command == "ledger" and arguments.ledger_command == "import":
            content: Optional[bytes] = None
            try:
                content = _read_bounded_bytes(
                    arguments.file, maximum=MAX_LEDGER_BYTES, label="ledger"
                )
                importers = {"jsonl": import_jsonl, "csv": import_csv}
                imported_events = importers[arguments.format](
                    content, expected_sha256=arguments.sha256
                )
                import_result = manager.import_events(imported_events, dry_run=arguments.dry_run)
            except CostManagerError as exc:
                if arguments.errors_file is not None:
                    error_record = {
                        "error": str(exc),
                        "format": "samsarix-ledger-import-errors",
                        "format_version": 1,
                    }
                    if content is not None:
                        error_record["source_sha256"] = hashlib.sha256(content).hexdigest()
                    error_content = (
                        json.dumps(
                            error_record,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        + "\n"
                    ).encode("utf-8")
                    _write_artifact(arguments.errors_file, error_content, force=False)
                raise
            assert content is not None
            payload = import_result.to_dict()
            payload["sha256"] = hashlib.sha256(content).hexdigest()
            if arguments.json:
                _emit_json(payload)
            else:
                verb = "Would import" if arguments.dry_run else "Imported"
                count = import_result.would_create if arguments.dry_run else import_result.created
                print(
                    f"{verb} {count} events; "
                    f"{import_result.existing} already existed "
                    f"(sha256 {payload['sha256']})."
                )
            return 0

        if arguments.command == "ledger" and arguments.ledger_command == "reconcile":
            reconciliation_events = manager.list_events(
                since=arguments.period_start, until=arguments.period_end
            )
            reconciliation = reconcile_invoice(
                reconciliation_events,
                provider=arguments.provider,
                billing_period_start=arguments.period_start,
                billing_period_end=arguments.period_end,
                billed_total=arguments.billed_usd,
                invoice_id=arguments.invoice_id,
                tolerance=arguments.tolerance,
            )
            if arguments.json:
                _emit_json(reconciliation.to_dict())
            else:
                status = "MATCH" if reconciliation.reconciled else "VARIANCE"
                print(
                    f"{status}: local {_format_usd(reconciliation.local_total)}, "
                    f"billed {_format_usd(reconciliation.billed_total)}, "
                    f"difference {_format_usd(reconciliation.variance)} across "
                    f"{reconciliation.events} events."
                )
            return 0 if reconciliation.reconciled else EXIT_RECONCILIATION_VARIANCE

        if arguments.command == "budget" and arguments.budget_command == "set":
            manager.set_budget(
                limit_usd=arguments.amount,
                period=arguments.period,
                project=arguments.project,
            )
            payload = {
                "scope": arguments.project or "global",
                "period": arguments.period,
                "limit_usd": decimal_text(Decimal(arguments.amount)),
            }
            if arguments.json:
                _emit_json(payload)
            else:
                print(
                    f"Set {payload['scope']} {payload['period']} budget to "
                    f"{_format_usd(Decimal(arguments.amount))}."
                )
            return 0

        if arguments.command == "budget" and arguments.budget_command == "check":
            if arguments.amount is not None:
                if arguments.provider or arguments.model:
                    raise ValidationError(
                        "amount cannot be combined with provider/model token estimation"
                    )
                estimate = validated_decimal(arguments.amount, field="amount")
            else:
                if not arguments.provider or not arguments.model:
                    raise ValidationError("provide --amount or both --provider and --model")
                estimate = manager.estimate(
                    provider=arguments.provider,
                    model=arguments.model,
                    input_tokens=arguments.input_tokens,
                    output_tokens=arguments.output_tokens,
                    cached_input_tokens=arguments.cached_input_tokens,
                    cache_write_input_tokens=arguments.cache_write_input_tokens,
                    at=arguments.at,
                    price_plan=arguments.price_plan,
                    service_tier=arguments.service_tier,
                    region=arguments.region,
                ).total_usd
            statuses = manager.check_budgets(
                estimated_usd=estimate,
                project=arguments.project,
                at=arguments.at,
            )
            allowed = all(status.allowed for status in statuses)
            payload = {
                "allowed": allowed,
                "estimated_usd": decimal_text(estimate),
                "statuses": [status.to_dict() for status in statuses],
            }
            if arguments.json:
                _emit_json(payload)
            else:
                _render_budgets(statuses)
            return 0 if allowed else EXIT_BUDGET_EXCEEDED

    raise AssertionError("unreachable command")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the CLI and return a stable process exit code."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        return _dispatch(arguments)
    except CostManagerError as exc:
        if arguments.json:
            _emit_json({"error": str(exc), "type": type(exc).__name__})
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (OSError, sqlite3.Error) as exc:
        message = f"database operation failed: {exc}"
        if arguments.json:
            _emit_json({"error": message, "type": type(exc).__name__})
        else:
            print(f"error: {message}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
