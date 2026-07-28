"""Command-line interface for local token cost accounting."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple

from . import __version__
from .exceptions import CostManagerError, ValidationError
from .manager import VALID_GROUPS, VALID_PERIODS, CostManager, default_database_path
from .models import (
    BudgetStatus,
    SummaryRow,
    decimal_text,
    validated_decimal,
)

EXIT_ERROR = 2
EXIT_BUDGET_EXCEEDED = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="helix-cost",
        description=("Track provider-reported LLM token usage and calculate exact local USD cost."),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help=("SQLite database path (default: HELIX_COST_DB or the platform data directory)"),
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
    price_commands.add_parser("list", help="list configured price versions")

    estimate = subparsers.add_parser("estimate", help="calculate cost without recording usage")
    _add_usage_arguments(estimate, include_identity=False)

    record = subparsers.add_parser("record", help="record one costed usage event")
    _add_usage_arguments(record, include_identity=True)

    report = subparsers.add_parser("report", help="summarize recorded usage and cost")
    report.add_argument("--since", help="inclusive ISO-8601 date/timestamp")
    report.add_argument("--until", help="exclusive ISO-8601 date/timestamp")
    report.add_argument("--month", help="UTC month in YYYY-MM form")
    report.add_argument("--project", help="limit the report to one project")
    report.add_argument(
        "--group-by",
        choices=VALID_GROUPS,
        default="none",
        help="aggregation dimension (default: none)",
    )

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
    budget_check.add_argument("--project", help="apply project and global budgets")
    budget_check.add_argument("--at", help="ISO-8601 timestamp (defaults to now)")
    return parser


def _add_usage_arguments(parser: argparse.ArgumentParser, *, include_identity: bool) -> None:
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--input-tokens", type=int, default=0)
    parser.add_argument("--output-tokens", type=int, default=0)
    parser.add_argument("--cached-input-tokens", type=int, default=0)
    if include_identity:
        parser.add_argument(
            "--request-id",
            help="idempotency key; retries with the same usage return the original event",
        )
        parser.add_argument("--project", help="optional non-sensitive cost allocation label")
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


def _emit_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def _format_usd(value: Decimal) -> str:
    return f"${value:.12f}".rstrip("0").rstrip(".")


def _render_prices(prices: Sequence[Any]) -> None:
    if not prices:
        print("No model prices configured. Add one with `helix-cost price set`.")
        return
    headers = ("PROVIDER", "MODEL", "INPUT/1M", "OUTPUT/1M", "CACHED/1M", "EFFECTIVE")
    rows = [
        (
            price.provider,
            price.model,
            _format_usd(price.input_usd_per_million),
            _format_usd(price.output_usd_per_million),
            _format_usd(price.cached_input_usd_per_million),
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
            _format_usd(row.total_usd),
        )
        for row in rows
    ]
    _table(
        ("GROUP", "REQUESTS", "INPUT", "OUTPUT", "CACHED INPUT", "TOTAL USD"),
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


def _dispatch(arguments: argparse.Namespace) -> int:
    database = arguments.db or default_database_path()
    with CostManager(database) as manager:
        if arguments.command == "init":
            payload = {
                "database": str(manager.database.resolve()),
                "schema_version": 1,
            }
            if arguments.json:
                _emit_json(payload)
            else:
                print(f"Initialized {payload['database']} (schema 1).")
            return 0

        if arguments.command == "price" and arguments.price_command == "set":
            price = manager.set_price(
                provider=arguments.provider,
                model=arguments.model,
                input_usd_per_million=arguments.input_rate,
                output_usd_per_million=arguments.output_rate,
                cached_input_usd_per_million=arguments.cached_input_rate,
                effective_from=arguments.effective_from,
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
                at=arguments.at,
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
                request_id=arguments.request_id,
                project=arguments.project,
                occurred_at=arguments.occurred_at,
            )
            statuses = manager.check_budgets(
                estimated_usd=Decimal("0"),
                project=record_result.event.project,
                at=record_result.event.occurred_at,
            )
            payload = record_result.to_dict()
            payload["budget_statuses"] = [status.to_dict() for status in statuses]
            if arguments.json:
                _emit_json(payload)
            else:
                verb = "Recorded" if record_result.created else "Already recorded"
                print(
                    f"{verb} {record_result.event.event_id}: "
                    f"{_format_usd(record_result.event.cost.total_usd)}"
                )
                denied = [status for status in statuses if not status.allowed]
                if denied:
                    print(
                        "Warning: recorded spend exceeds an applicable budget; "
                        "use `budget check` before future calls.",
                        file=sys.stderr,
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
            )
            if arguments.json:
                _emit_json([row.to_dict() for row in rows])
            else:
                _render_report(rows)
            return 0

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
                    at=arguments.at,
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
