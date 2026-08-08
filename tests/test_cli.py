# Copyright 2026 Samsarix LLC
# SPDX-License-Identifier: Apache-2.0

"""Command-level coverage for the documented primary journey."""

import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import pytest

import samsarix_token_cost_manager.cli as cli_module
from samsarix_token_cost_manager.cli import main


@dataclass(frozen=True)
class Invocation:
    """Captured in-process CLI result."""

    returncode: int
    stdout: str
    stderr: str


def run_cli(
    database: Path,
    *arguments: str,
    json_output: bool = False,
) -> Invocation:
    command = ["--db", str(database)]
    if json_output:
        command.append("--json")
    command.extend(arguments)
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        returncode = main(command)
    return Invocation(returncode, stdout.getvalue(), stderr.getvalue())


def configure_price(database: Path) -> None:
    result = run_cli(
        database,
        "price",
        "set",
        "--provider",
        "example",
        "--model",
        "model-v1",
        "--input",
        "2.50",
        "--output",
        "10",
        "--cached-input",
        "0.25",
        "--effective-from",
        "2026-01-01",
    )
    assert result.returncode == 0


def test_help_and_version_are_real_process_entry_points() -> None:
    help_result = subprocess.run(
        [sys.executable, "-m", "samsarix_token_cost_manager", "--help"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    version_result = subprocess.run(
        [sys.executable, "-m", "samsarix_token_cost_manager", "--version"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONUTF8": "1"},
    )

    assert "price" in help_result.stdout
    assert "budget" in help_result.stdout
    assert version_result.stdout.strip() == "samsarix-cost 0.1.0"


def test_primary_journey_and_json_contract(tmp_path: Path) -> None:
    database = tmp_path / "costs.sqlite3"
    configure_price(database)

    recorded = run_cli(
        database,
        "record",
        "--provider",
        "example",
        "--model",
        "model-v1",
        "--input-tokens",
        "1000000",
        "--output-tokens",
        "500000",
        "--cached-input-tokens",
        "100000",
        "--project",
        "demo",
        "--request-id",
        "req-1",
        "--occurred-at",
        "2026-07-28T12:00:00Z",
        json_output=True,
    )
    retried = run_cli(
        database,
        "record",
        "--provider",
        "example",
        "--model",
        "model-v1",
        "--input-tokens",
        "1000000",
        "--output-tokens",
        "500000",
        "--cached-input-tokens",
        "100000",
        "--project",
        "demo",
        "--request-id",
        "req-1",
        json_output=True,
    )
    report = run_cli(
        database,
        "report",
        "--month",
        "2026-07",
        "--group-by",
        "project",
        json_output=True,
    )

    first_payload = json.loads(recorded.stdout)
    retry_payload = json.loads(retried.stdout)
    report_payload = json.loads(report.stdout)
    assert recorded.returncode == retried.returncode == report.returncode == 0
    assert first_payload["created"] is True
    assert retry_payload["created"] is False
    assert first_payload["event"]["event_id"] == retry_payload["event"]["event_id"]
    assert first_payload["event"]["cost"]["total_usd"] == "7.525000000000"
    assert report_payload == [
        {
            "cache_write_input_tokens": 0,
            "cached_input_tokens": 100000,
            "group": "demo",
            "input_tokens": 1000000,
            "output_tokens": 500000,
            "requests": 1,
            "total_usd": "7.525000000000",
        }
    ]


def test_human_output_covers_init_price_estimate_record_and_report(tmp_path: Path) -> None:
    database = tmp_path / "human.sqlite3"
    initialized = run_cli(database, "init")
    configure_price(database)
    prices = run_cli(database, "price", "list")
    estimate = run_cli(
        database,
        "estimate",
        "--provider",
        "example",
        "--model",
        "model-v1",
        "--input-tokens",
        "1000000",
        "--at",
        "2026-07-01",
    )
    recorded = run_cli(
        database,
        "record",
        "--provider",
        "example",
        "--model",
        "model-v1",
        "--input-tokens",
        "1",
        "--occurred-at",
        "2026-07-01",
    )
    report = run_cli(database, "report", "--group-by", "model")

    assert initialized.returncode == 0
    assert "Initialized" in initialized.stdout
    assert "example" in prices.stdout and "INPUT/1M" in prices.stdout
    assert estimate.stdout.strip() == "Estimated cost: $2.5"
    assert "Recorded evt_" in recorded.stdout
    assert "example/model-v1" in report.stdout


def test_empty_states_and_missing_price_are_explicit(tmp_path: Path) -> None:
    database = tmp_path / "empty.sqlite3"
    prices = run_cli(database, "price", "list")
    report = run_cli(database, "report")
    last_month = run_cli(database, "report", "--month", "9999-12")
    missing = run_cli(
        database,
        "estimate",
        "--provider",
        "missing",
        "--model",
        "model",
        "--input-tokens",
        "1",
        json_output=True,
    )

    assert "No model prices" in prices.stdout
    assert "No usage events" in report.stdout
    assert last_month.returncode == 0
    assert "No usage events" in last_month.stdout
    assert missing.returncode == 2
    assert json.loads(missing.stdout)["type"] == "PriceNotFoundError"


def test_budget_allow_denial_and_post_record_warning(tmp_path: Path) -> None:
    database = tmp_path / "budget.sqlite3"
    configure_price(database)

    no_budget = run_cli(database, "budget", "check", "--amount", "100")
    budget_set = run_cli(
        database,
        "budget",
        "set",
        "--amount",
        "1",
        "--period",
        "monthly",
        "--project",
        "demo",
        json_output=True,
    )
    denied = run_cli(
        database,
        "budget",
        "check",
        "--provider",
        "example",
        "--model",
        "model-v1",
        "--input-tokens",
        "1000000",
        "--project",
        "demo",
        "--at",
        "2026-07-28",
        json_output=True,
    )
    recorded = run_cli(
        database,
        "record",
        "--provider",
        "example",
        "--model",
        "model-v1",
        "--input-tokens",
        "1000000",
        "--project",
        "demo",
        "--occurred-at",
        "2026-07-28",
    )

    payload = json.loads(denied.stdout)
    assert "No applicable budgets" in no_budget.stdout
    assert json.loads(budget_set.stdout)["scope"] == "demo"
    assert denied.returncode == 3
    assert payload["allowed"] is False
    assert payload["statuses"][0]["scope"] == "demo"
    assert recorded.returncode == 0
    assert "exceeds an applicable budget" in recorded.stderr


def test_cli_validation_and_database_errors_are_actionable(tmp_path: Path) -> None:
    database = tmp_path / "errors.sqlite3"
    configure_price(database)
    bad_month = run_cli(database, "report", "--month", "2026-99")
    overlapping_filters = run_cli(database, "report", "--month", "2026-07", "--since", "2026-01-01")
    bad_budget_shape = run_cli(
        database,
        "budget",
        "check",
        "--amount",
        "1",
        "--provider",
        "example",
        "--model",
        "model-v1",
    )
    missing_budget_shape = run_cli(database, "budget", "check")
    invalid_amount = run_cli(database, "budget", "check", "--amount", "nan")
    directory_as_database = run_cli(tmp_path, "init", json_output=True)

    assert bad_month.returncode == 2 and "YYYY-MM" in bad_month.stderr
    assert (
        overlapping_filters.returncode == 2 and "cannot be combined" in overlapping_filters.stderr
    )
    assert bad_budget_shape.returncode == 2 and "cannot be combined" in bad_budget_shape.stderr
    assert (
        missing_budget_shape.returncode == 2 and "provide --amount" in missing_budget_shape.stderr
    )
    assert invalid_amount.returncode == 2 and "finite" in invalid_amount.stderr
    assert directory_as_database.returncode == 2
    assert "database operation failed" in json.loads(directory_as_database.stdout)["error"]


def test_conflicting_idempotency_key_is_an_error(tmp_path: Path) -> None:
    database = tmp_path / "conflict.sqlite3"
    configure_price(database)
    base = [
        "record",
        "--provider",
        "example",
        "--model",
        "model-v1",
        "--request-id",
        "req-1",
    ]
    assert run_cli(database, *base, "--input-tokens", "1").returncode == 0
    conflict = run_cli(database, *base, "--input-tokens", "2")

    assert conflict.returncode == 2
    assert "different usage" in conflict.stderr


def test_ingest_openai_payload_and_report_allocation_dimension(tmp_path: Path) -> None:
    database = tmp_path / "ingest.sqlite3"
    run_cli(
        database,
        "price",
        "set",
        "--provider",
        "openai",
        "--model",
        "model-v1",
        "--input",
        "2.50",
        "--output",
        "10",
        "--cached-input",
        "0.25",
        "--effective-from",
        "2026-01-01",
    )
    payload = tmp_path / "openai-response.json"
    payload.write_text(
        json.dumps(
            {
                "id": "resp_ingest_1",
                "model": "model-v1",
                "usage": {
                    "input_tokens": 1_000,
                    "output_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 600},
                },
            }
        ),
        encoding="utf-8",
    )

    ingested = run_cli(
        database,
        "ingest",
        "--format",
        "openai",
        "--file",
        str(payload),
        "--project",
        "assistant",
        "--dimension",
        "team=product",
        "--dimension",
        "feature=answer",
        json_output=True,
    )
    report = run_cli(
        database,
        "report",
        "--dimension",
        "feature=answer",
        "--group-by",
        "dimension:team",
        json_output=True,
    )

    ingest_payload = json.loads(ingested.stdout)
    assert ingested.returncode == 0
    assert ingest_payload["source_format"] == "openai"
    assert ingest_payload["event"]["input_tokens"] == 400
    assert ingest_payload["event"]["cached_input_tokens"] == 600
    assert ingest_payload["event"]["dimensions"] == {
        "feature": "answer",
        "team": "product",
    }
    assert json.loads(report.stdout)[0]["group"] == "product"


def test_ingest_stdin_human_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "stdin-ingest.sqlite3"
    run_cli(
        database,
        "price",
        "set",
        "--provider",
        "anthropic",
        "--model",
        "claude-test",
        "--input",
        "3",
        "--output",
        "15",
        "--cached-input",
        "0.3",
        "--cache-write-input",
        "3.75",
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "id": "msg_stdin",
                    "model": "claude-test",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "cache_read_input_tokens": 20,
                        "cache_creation_input_tokens": 5,
                    },
                }
            )
        ),
    )
    ingested = run_cli(database, "ingest", "--format", "anthropic", "--file", "-")

    assert ingested.returncode == 0
    assert "from anthropic" in ingested.stdout


def test_report_rejects_duplicate_dimension_keys(tmp_path: Path) -> None:
    result = run_cli(
        tmp_path / "duplicate-dimension.sqlite3",
        "report",
        "--dimension",
        "team=one",
        "--dimension",
        "team=two",
    )

    assert result.returncode == 2
    assert "more than once" in result.stderr


@pytest.mark.parametrize(
    ("payload_bytes", "max_bytes", "expected"),
    (
        (b"[]", None, "JSON object"),
        (b"\xff", None, "could not read"),
        (b"{}", 1, "must not exceed"),
    ),
)
def test_ingest_rejects_invalid_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload_bytes: bytes,
    max_bytes: int | None,
    expected: str,
) -> None:
    database = tmp_path / "invalid-ingest.sqlite3"
    payload = tmp_path / "payload.json"
    payload.write_bytes(payload_bytes)
    if max_bytes is not None:
        monkeypatch.setattr(cli_module, "MAX_INGEST_BYTES", max_bytes)

    result = run_cli(
        database,
        "ingest",
        "--format",
        "otel",
        "--file",
        str(payload),
    )

    assert result.returncode == 2
    assert expected in result.stderr
