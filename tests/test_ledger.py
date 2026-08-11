# Copyright 2026 Samsarix LLC
# SPDX-License-Identifier: Apache-2.0

"""Portable ledger contract and digest coverage."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest

from samsarix_token_cost_manager import CostManager, DuplicateRequestError, ValidationError
from samsarix_token_cost_manager.ledger import (
    export_csv,
    export_jsonl,
    import_csv,
    import_jsonl,
    reconcile_invoice,
    verify_digest,
    write_jsonl,
)


def _events(manager: CostManager):  # type: ignore[no-untyped-def]
    manager.set_price(
        provider="example",
        model="model-v1",
        input_usd_per_million="2.5",
        output_usd_per_million="10",
        cached_input_usd_per_million="0.25",
        cache_write_input_usd_per_million="3.125",
        effective_from="2026-01-01",
    )
    manager.record(
        provider="example",
        model="model-v1",
        request_id="req-later",
        project="support",
        input_tokens=100,
        output_tokens=20,
        dimensions={"team": "success", "tenant": "customer-a"},
        occurred_at="2026-02-02T00:00:00Z",
    )
    manager.record(
        provider="example",
        model="model-v1",
        request_id="req-first",
        project="platform",
        input_tokens=200,
        cached_input_tokens=50,
        cache_write_input_tokens=25,
        occurred_at="2026-02-01T00:00:00Z",
    )
    return manager.list_events()


def test_jsonl_export_is_canonical_deterministic_and_verifiable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with CostManager(tmp_path / "ledger.sqlite3") as manager:
        events = _events(manager)
        first = export_jsonl(reversed(events))
        second = export_jsonl(events)

    assert first == second
    assert first.records == 2
    assert first.total_usd == sum((event.cost.total_usd for event in events), Decimal("0"))
    assert first.sha256 == hashlib.sha256(first.content).hexdigest()
    streamed = io.BytesIO()
    streamed_result = write_jsonl(streamed, iter(events))
    assert streamed.getvalue() == first.content
    assert streamed_result.sha256 == first.sha256 and streamed_result.records == 2
    assert verify_digest(first.content, first.sha256.upper()) == first.sha256
    lines = [json.loads(line) for line in first.content.decode("utf-8").splitlines()]
    assert lines[0] == {
        "format": "samsarix-usage-ledger",
        "format_version": 2,
        "type": "manifest",
    }
    assert [line["record"]["request_id"] for line in lines[1:]] == ["req-first", "req-later"]
    assert lines[2]["record"]["dimensions"] == {
        "team": "success",
        "tenant": "customer-a",
    }


def test_jsonl_v1_import_defaults_price_book_selectors(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with CostManager(tmp_path / "ledger.sqlite3") as manager:
        events = _events(manager)
        lines = [json.loads(line) for line in export_jsonl(events).content.decode().splitlines()]
    lines[0]["format_version"] = 1
    for envelope in lines[1:]:
        for field in (
            "price_plan",
            "service_tier",
            "region",
            "price_input_token_min",
            "price_input_token_max",
        ):
            envelope["record"].pop(field)
    legacy = (
        "\n".join(json.dumps(line, sort_keys=True, separators=(",", ":")) for line in lines) + "\n"
    ).encode()

    assert import_jsonl(legacy) == events


@pytest.mark.parametrize(
    "field",
    (
        "price_plan",
        "service_tier",
        "region",
        "price_input_token_min",
        "price_input_token_max",
    ),
)
def test_jsonl_v2_requires_price_book_provenance(tmp_path, field: str) -> None:  # type: ignore[no-untyped-def]
    with CostManager(tmp_path / "ledger.sqlite3") as manager:
        lines = [
            json.loads(line)
            for line in export_jsonl(_events(manager)).content.decode().splitlines()
        ]
    lines[1]["record"].pop(field)
    content = (
        "\n".join(json.dumps(line, sort_keys=True, separators=(",", ":")) for line in lines) + "\n"
    ).encode()

    with pytest.raises(ValidationError, match=f"missing '{field}'"):
        import_jsonl(content)


def test_csv_export_is_rfc_compatible_and_exact(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with CostManager(tmp_path / "ledger.sqlite3") as manager:
        events = _events(manager)
        artifact = export_csv(events)

    text = artifact.content.decode("utf-8")
    assert "\r\n" in text
    rows = list(csv.DictReader(io.StringIO(text, newline="")))
    assert [row["request_id"] for row in rows] == ["req-first", "req-later"]
    assert rows[1]["dimensions_json"] == '{"team":"success","tenant":"customer-a"}'
    assert rows[0]["cache_write_input_rate"] == "3.125"
    assert import_csv(artifact.content, expected_sha256=artifact.sha256) == events


def test_list_events_filters_and_digest_validation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with CostManager(tmp_path / "ledger.sqlite3") as manager:
        _events(manager)
        assert len(manager.list_events(since="2026-02-02")) == 1
        assert len(manager.list_events(until="2026-02-02")) == 1
        assert manager.list_events(project="support")[0].request_id == "req-later"
        with pytest.raises(ValidationError, match="since must be earlier than until"):
            manager.list_events(since="2026-02-02", until="2026-02-02")

    with pytest.raises(ValidationError, match="64"):
        verify_digest(b"ledger", "bad")
    with pytest.raises(ValidationError, match="mismatch"):
        verify_digest(b"ledger", "0" * 64)


def test_jsonl_import_dry_run_round_trip_and_idempotency(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with CostManager(tmp_path / "source.sqlite3") as source:
        artifact = export_jsonl(_events(source))
        source_events = source.list_events()
    imported_events = import_jsonl(artifact.content, expected_sha256=artifact.sha256)

    with CostManager(tmp_path / "target.sqlite3") as target:
        dry_run = target.import_events(imported_events, dry_run=True)
        assert target.list_events() == []
        imported = target.import_events(imported_events)
        repeated = target.import_events(imported_events)
        assert target.list_events() == source_events

    assert dry_run.created == 0 and dry_run.would_create == 2 and dry_run.dry_run is True
    assert imported.created == 2 and imported.existing == 0
    assert repeated.created == 0 and repeated.existing == 2


def test_jsonl_import_rejects_tampered_cost_before_writing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with CostManager(tmp_path / "source.sqlite3") as source:
        artifact = export_jsonl(_events(source))
    lines = artifact.content.decode("utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["record"]["total_cost"] = "999"
    lines[1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    content = ("\n".join(lines) + "\n").encode()

    with pytest.raises(ValidationError, match="total_cost"):
        import_jsonl(content)
    with pytest.raises(ValidationError, match="mismatch"):
        import_jsonl(content, expected_sha256=artifact.sha256)


def test_invoice_reconciliation_reports_exact_variance_and_tolerance(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with CostManager(tmp_path / "ledger.sqlite3") as manager:
        events = _events(manager)
    local = sum((event.cost.total_usd for event in events), Decimal("0"))
    matched = reconcile_invoice(
        events,
        provider="example",
        billing_period_start="2026-02-01",
        billing_period_end="2026-03-01",
        billed_total=local + Decimal("0.005"),
        invoice_id="inv-2026-02",
        tolerance="0.01",
    )
    mismatched = reconcile_invoice(
        events,
        provider="example",
        billing_period_start="2026-02-01",
        billing_period_end="2026-03-01",
        billed_total=local + Decimal("1"),
    )

    assert matched.reconciled is True
    assert matched.variance == Decimal("0.005000000000")
    assert matched.events == 2 and matched.invoice_id == "inv-2026-02"
    assert mismatched.reconciled is False
    with pytest.raises(
        ValidationError,
        match="billing_period_start must be earlier than billing_period_end",
    ):
        reconcile_invoice(
            events,
            provider="example",
            billing_period_start="2026-03-01",
            billing_period_end="2026-02-01",
            billed_total="0",
        )


def test_import_conflict_analysis_prevents_partial_batch(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with CostManager(tmp_path / "source.sqlite3") as source:
        events = _events(source)
    with CostManager(tmp_path / "target.sqlite3") as target:
        target.set_price(
            provider="example",
            model="model-v1",
            input_usd_per_million="2.5",
            output_usd_per_million="10",
            effective_from="2026-01-01",
        )
        target.record(
            provider="example",
            model="model-v1",
            request_id="req-later",
            input_tokens=999,
            occurred_at="2026-02-02",
        )
        with pytest.raises(DuplicateRequestError, match="conflicts"):
            target.import_events(events)
        remaining = target.list_events()

    assert len(remaining) == 1
    assert remaining[0].request_id == "req-later"


def test_separate_connections_serialize_import_conflict_check_and_write(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with CostManager(tmp_path / "source.sqlite3") as source:
        event = _events(source)[0]
    database = tmp_path / "target.sqlite3"
    first = CostManager(database)
    second = CostManager(database)
    first.open()
    second.open()
    barrier = Barrier(2)

    def import_one(manager: CostManager) -> tuple[int, int]:
        barrier.wait()
        result = manager.import_events([event])
        return result.created, result.existing

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(import_one, (first, second)))
    finally:
        first.close()
        second.close()

    assert sorted(results) == [(0, 1), (1, 0)]
