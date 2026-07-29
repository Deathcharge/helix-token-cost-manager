# Copyright 2026 Samsarix LLC
# SPDX-License-Identifier: Apache-2.0

"""API-level coverage for exact accounting and persistence behavior."""

import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest

from samsarix_token_cost_manager import (
    CostManager,
    DuplicateRequestError,
    PriceNotFoundError,
    ValidationError,
)
from samsarix_token_cost_manager.manager import default_database_path
from samsarix_token_cost_manager.models import (
    format_timestamp,
    money,
    parse_timestamp,
    validated_decimal,
    validated_text,
    validated_tokens,
)


def test_estimate_uses_mutually_exclusive_token_buckets(manager: CostManager) -> None:
    result = manager.estimate(
        provider="example",
        model="model-v1",
        input_tokens=1_000_000,
        output_tokens=500_000,
        cached_input_tokens=100_000,
        at="2026-07-01",
    )

    assert result.input_usd == Decimal("2.500000000000")
    assert result.output_usd == Decimal("5.000000000000")
    assert result.cached_input_usd == Decimal("0.025000000000")
    assert result.total_usd == Decimal("7.525000000000")


def test_price_resolution_and_snapshots_are_time_versioned(
    manager: CostManager,
) -> None:
    manager.set_price(
        provider="example",
        model="model-v1",
        input_usd_per_million="1",
        output_usd_per_million="4",
        effective_from="2026-08-01",
    )

    july = manager.record(
        provider="example",
        model="model-v1",
        input_tokens=1_000_000,
        occurred_at="2026-07-31T23:59:59Z",
    ).event
    august = manager.record(
        provider="example",
        model="model-v1",
        input_tokens=1_000_000,
        occurred_at="2026-08-01T00:00:00Z",
    ).event

    assert july.cost.total_usd == Decimal("2.500000000000")
    assert july.cost.price.input_usd_per_million == Decimal("2.50")
    assert august.cost.total_usd == Decimal("1.000000000000")
    assert august.cost.price.cached_input_usd_per_million == Decimal("1")


def test_missing_price_fails_closed(manager: CostManager) -> None:
    with pytest.raises(PriceNotFoundError, match="add one"):
        manager.estimate(provider="unknown", model="model", input_tokens=1)


def test_prices_are_upserted_and_listed_deterministically(manager: CostManager) -> None:
    manager.set_price(
        provider="zeta",
        model="other",
        input_usd_per_million="1",
        output_usd_per_million="2",
        effective_from="2026-01-01",
    )
    updated = manager.set_price(
        provider="example",
        model="model-v1",
        input_usd_per_million="3",
        output_usd_per_million="4",
        effective_from="2026-01-01",
    )

    prices = manager.list_prices()
    assert [(price.provider, price.model) for price in prices] == [
        ("example", "model-v1"),
        ("zeta", "other"),
    ]
    assert prices[0] == updated


def test_request_id_is_idempotent_and_conflicts_are_rejected(
    manager: CostManager,
) -> None:
    first = manager.record(
        provider="example",
        model="model-v1",
        input_tokens=10,
        output_tokens=5,
        project="demo",
        request_id="req-1",
        occurred_at="2026-07-15T12:00:00Z",
    )
    retry = manager.record(
        provider="example",
        model="model-v1",
        input_tokens=10,
        output_tokens=5,
        project="demo",
        request_id="req-1",
    )

    assert first.created is True
    assert retry.created is False
    assert retry.event == first.event

    with pytest.raises(DuplicateRequestError, match="different usage"):
        manager.record(
            provider="example",
            model="model-v1",
            input_tokens=11,
            output_tokens=5,
            project="demo",
            request_id="req-1",
        )


def test_reports_filter_and_group_without_float_drift(manager: CostManager) -> None:
    events = (
        ("2026-07-01", "alpha", 1),
        ("2026-07-02", "alpha", 2),
        ("2026-08-01", "beta", 3),
    )
    for index, (occurred_at, project, tokens) in enumerate(events):
        manager.record(
            provider="example",
            model="model-v1",
            input_tokens=tokens,
            project=project,
            request_id=f"req-{index}",
            occurred_at=occurred_at,
        )

    rows = manager.summarize(
        since="2026-07-01",
        until="2026-08-01",
        group_by="project",
    )
    assert [row.group for row in rows] == ["alpha"]
    assert rows[0].requests == 2
    assert rows[0].input_tokens == 3
    assert rows[0].total_usd == Decimal("0.000007500000")
    assert manager.summarize(project="missing") == []


@pytest.mark.parametrize(
    ("group_by", "groups"),
    [
        ("none", ["all"]),
        ("provider", ["example"]),
        ("model", ["example/model-v1"]),
        ("project", ["(unassigned)", "demo"]),
        ("day", ["2026-07-01", "2026-08-02"]),
        ("month", ["2026-07", "2026-08"]),
    ],
)
def test_all_report_groups(manager: CostManager, group_by: str, groups: list[str]) -> None:
    manager.record(
        provider="example",
        model="model-v1",
        input_tokens=1,
        occurred_at="2026-07-01",
    )
    manager.record(
        provider="example",
        model="model-v1",
        output_tokens=1,
        project="demo",
        occurred_at="2026-08-02",
    )

    assert [row.group for row in manager.summarize(group_by=group_by)] == groups


def test_report_filter_validation(manager: CostManager) -> None:
    with pytest.raises(ValidationError, match="group_by"):
        manager.summarize(group_by="sql please")
    with pytest.raises(ValidationError, match="earlier"):
        manager.summarize(since="2026-08-01", until="2026-07-01")
    with pytest.raises(ValidationError, match="project"):
        manager.summarize(project="*")


def test_global_and_project_budgets_are_both_enforced(manager: CostManager) -> None:
    manager.record(
        provider="example",
        model="model-v1",
        input_tokens=1_000_000,
        project="demo",
        occurred_at="2026-07-10",
    )
    manager.set_budget(limit_usd="5", period="monthly")
    manager.set_budget(limit_usd="3", period="monthly", project="demo")

    statuses = manager.check_budgets(estimated_usd="0.75", project="demo", at="2026-07-20")
    by_scope = {status.scope: status for status in statuses}

    assert by_scope["global"].spent_usd == Decimal("2.500000000000")
    assert by_scope["global"].allowed is True
    assert by_scope["demo"].projected_usd == Decimal("3.250000000000")
    assert by_scope["global"].remaining_usd == Decimal("1.750000000000")
    assert by_scope["demo"].remaining_usd == Decimal("0")
    assert by_scope["demo"].allowed is False


def test_daily_budget_uses_utc_boundaries(manager: CostManager) -> None:
    manager.record(
        provider="example",
        model="model-v1",
        input_tokens=400_000,
        occurred_at="2026-07-28T23:59:59-04:00",
    )
    manager.set_budget(limit_usd="2", period="daily")

    statuses = manager.check_budgets(estimated_usd="1.01", at="2026-07-29T12:00:00Z")
    assert statuses[0].spent_usd == Decimal("1.000000000000")
    assert statuses[0].allowed is False


def test_budget_validation(manager: CostManager) -> None:
    with pytest.raises(ValidationError, match="period"):
        manager.set_budget(limit_usd="1", period="weekly")
    with pytest.raises(ValidationError, match="positive"):
        manager.set_budget(limit_usd="0", period="daily")
    with pytest.raises(ValidationError, match="project"):
        manager.set_budget(limit_usd="1", period="daily", project="*")


def test_shared_manager_serializes_threads(manager: CostManager) -> None:
    def record(index: int) -> str:
        return manager.record(
            provider="example",
            model="model-v1",
            input_tokens=1,
            request_id=f"thread-{index}",
            occurred_at="2026-07-01",
        ).event.event_id

    with ThreadPoolExecutor(max_workers=8) as executor:
        event_ids = list(executor.map(record, range(40)))

    assert len(set(event_ids)) == 40
    assert manager.summarize()[0].requests == 40


def test_separate_connections_reconcile_idempotency_race(
    manager: CostManager, tmp_path: Path
) -> None:
    second = CostManager(tmp_path / "costs.sqlite3")
    barrier = Barrier(2)

    def record(cost_manager: CostManager) -> tuple[bool, str]:
        barrier.wait()
        result = cost_manager.record(
            provider="example",
            model="model-v1",
            input_tokens=1,
            request_id="shared-request",
            occurred_at="2026-07-01",
        )
        return result.created, result.event.event_id

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(record, (manager, second)))
    finally:
        second.close()

    assert sorted(created for created, _ in results) == [False, True]
    assert len({event_id for _, event_id in results}) == 1


def test_sql_metacharacters_remain_data(manager: CostManager) -> None:
    price = manager.set_price(
        provider="provider'--",
        model='model"; DROP TABLE usage_events;--',
        input_usd_per_million="1",
        output_usd_per_million="0",
    )
    result = manager.record(
        provider=price.provider,
        model=price.model,
        input_tokens=1,
        project="project' OR 1=1;--",
    )

    assert result.created is True
    assert manager.summarize(project="project' OR 1=1;--")[0].requests == 1


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"provider": "", "model": "x", "input_tokens": 1}, "provider"),
        ({"provider": "x", "model": "y", "input_tokens": -1}, "non-negative"),
        (
            {"provider": "x", "model": "y", "input_tokens": 1_000_000_000_001},
            "must not exceed",
        ),
    ],
)
def test_estimate_validates_untrusted_input(
    manager: CostManager, kwargs: dict, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        manager.estimate(**kwargs)


def test_newer_database_schema_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version = 99")
    connection.close()

    with pytest.raises(ValidationError, match="newer than supported"):
        CostManager(database).open()

    # A failed open releases the handle instead of leaving a future schema locked.
    database.unlink()


def test_manager_lifecycle_and_environment_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "configured.sqlite3"
    monkeypatch.setenv("SAMSARIX_COST_DB", str(configured))
    assert default_database_path() == configured

    manager = CostManager()
    assert manager.open() is manager
    assert manager.open() is manager
    manager.close()
    manager.close()
    assert configured.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not available on Windows")
def test_new_database_uses_private_posix_permissions(tmp_path: Path) -> None:
    database = tmp_path / "private" / "costs.sqlite3"

    with CostManager(database):
        pass

    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("value", "field", "message"),
    [
        ("", "empty", "must not be empty"),
        ("line\nbreak", "line", "single line"),
        ("nul\x00byte", "nul", "NUL"),
        ("ansi\x1b[31m", "ansi", "control"),
        ("zero\u200bwidth", "format", "formatting"),
        ("x" * 201, "long", "at most"),
    ],
)
def test_text_validation(value: str, field: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        validated_text(value, field=field)

    assert validated_text("  ok  ", field="normal") == "ok"
    assert validated_text("", field="optional", required=False) == ""


@pytest.mark.parametrize("value", [True, "1", 1.2, -1, 1_000_000_000_001])
def test_token_validation_rejects_wrong_or_unbounded_values(value: object) -> None:
    with pytest.raises(ValidationError):
        validated_tokens(value, field="tokens")  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, "not-a-number", "Infinity", "-1", "1000001"])
def test_decimal_validation_rejects_wrong_or_unbounded_values(value: object) -> None:
    with pytest.raises(ValidationError):
        validated_decimal(  # type: ignore[arg-type]
            value, field="rate", maximum=Decimal("1000000")
        )


def test_timestamp_parsing_and_formatting() -> None:
    assert parse_timestamp(date(2026, 7, 28), field="date") == datetime(
        2026, 7, 28, tzinfo=timezone.utc
    )
    assert parse_timestamp(datetime(2026, 7, 28, 12, 0), field="naive") == datetime(
        2026, 7, 28, 12, 0, tzinfo=timezone.utc
    )
    assert (
        format_timestamp(parse_timestamp("2026-07-28T08:00:00-04:00", field="offset"))
        == "2026-07-28T12:00:00.000000Z"
    )
    assert parse_timestamp(None, field="now").tzinfo == timezone.utc

    for invalid in ("", "not-a-date", 42):
        with pytest.raises(ValidationError):
            parse_timestamp(invalid, field="timestamp")  # type: ignore[arg-type]


def test_money_uses_sufficient_precision_for_large_aggregates() -> None:
    assert money(Decimal("12345678901234567890123456789.123")) == Decimal(
        "12345678901234567890123456789.123000000000"
    )


def test_last_calendar_period_has_an_open_upper_bound(manager: CostManager) -> None:
    manager.set_budget(limit_usd="1", period="daily")
    manager.set_budget(limit_usd="1", period="monthly")

    statuses = manager.check_budgets(at="9999-12-31T23:59:59.999999Z")

    assert len(statuses) == 2
    assert all(status.allowed for status in statuses)
