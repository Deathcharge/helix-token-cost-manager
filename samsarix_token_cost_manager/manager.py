# Copyright 2026 Samsarix LLC
# SPDX-License-Identifier: Apache-2.0

"""SQLite-backed, local-first token usage and cost accounting."""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Tuple, Union

from .adapters import UsageMeasurement
from .exceptions import DuplicateRequestError, PriceNotFoundError, ValidationError
from .models import (
    MAX_RATE_USD_PER_MILLION,
    BudgetStatus,
    CostBreakdown,
    DecimalInput,
    ModelPrice,
    RecordResult,
    SummaryRow,
    UsageEvent,
    decimal_text,
    format_timestamp,
    money,
    parse_timestamp,
    utc_now,
    validated_decimal,
    validated_dimensions,
    validated_text,
    validated_tokens,
)

SCHEMA_VERSION = 2
GLOBAL_SCOPE = "*"
VALID_PERIODS = ("daily", "monthly")
VALID_GROUPS = ("none", "provider", "model", "project", "day", "month")
MILLION = Decimal(1_000_000)


def default_database_path() -> Path:
    """Return the platform-appropriate default database path."""

    configured = os.environ.get("SAMSARIX_COST_DB")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "samsarix-token-cost-manager" / "costs.sqlite3"


class CostManager:
    """Record immutable usage events and query exact local cost summaries.

    A manager owns one SQLite connection and may be used as a context manager. One
    instance is safe to share between threads; separate processes coordinate through
    SQLite's WAL journal and a bounded busy timeout.
    """

    def __init__(self, database: Optional[Union[str, os.PathLike[str]]] = None):
        self.database = Path(database).expanduser() if database else default_database_path()
        self._connection: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    def __enter__(self) -> "CostManager":
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def open(self) -> "CostManager":
        """Create/open the database, apply safe pragmas, and initialize its schema."""

        with self._lock:
            if self._connection is not None:
                return self
            self.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            existed = self.database.exists()
            connection = sqlite3.connect(
                str(self.database),
                timeout=5.0,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            self._connection = connection
            try:
                self._initialize_schema()
            except Exception:
                connection.close()
                self._connection = None
                raise
            if not existed and os.name != "nt":
                with suppress(OSError):
                    self.database.chmod(0o600)
        return self

    def close(self) -> None:
        """Close the owned SQLite connection."""

        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the initialized connection."""

        if self._connection is None:
            self.open()
        if self._connection is None:
            raise RuntimeError("database connection did not initialize")
        return self._connection

    def _initialize_schema(self) -> None:
        connection = self.connection
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise ValidationError(
                f"database schema {version} is newer than supported schema {SCHEMA_VERSION}"
            )
        if version == 0:
            with connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS model_prices (
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        effective_from TEXT NOT NULL,
                        input_rate TEXT NOT NULL,
                        output_rate TEXT NOT NULL,
                        cached_input_rate TEXT NOT NULL,
                        cache_write_input_rate TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (provider, model, effective_from)
                    );

                    CREATE TABLE IF NOT EXISTS usage_events (
                        event_id TEXT PRIMARY KEY,
                        request_id TEXT UNIQUE,
                        occurred_at TEXT NOT NULL,
                        recorded_at TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        project TEXT,
                        input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
                        output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
                        cached_input_tokens INTEGER NOT NULL CHECK (cached_input_tokens >= 0),
                        cache_write_input_tokens INTEGER NOT NULL
                            CHECK (cache_write_input_tokens >= 0),
                        input_rate TEXT NOT NULL,
                        output_rate TEXT NOT NULL,
                        cached_input_rate TEXT NOT NULL,
                        cache_write_input_rate TEXT NOT NULL,
                        price_effective_from TEXT NOT NULL,
                        input_cost TEXT NOT NULL,
                        output_cost TEXT NOT NULL,
                        cached_input_cost TEXT NOT NULL,
                        cache_write_input_cost TEXT NOT NULL,
                        total_cost TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS usage_events_occurred_at
                    ON usage_events (occurred_at);
                    CREATE INDEX IF NOT EXISTS usage_events_project_occurred_at
                    ON usage_events (project, occurred_at);

                    CREATE TABLE IF NOT EXISTS event_dimensions (
                        event_id TEXT NOT NULL REFERENCES usage_events(event_id) ON DELETE CASCADE,
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        PRIMARY KEY (event_id, key)
                    );
                    CREATE INDEX IF NOT EXISTS event_dimensions_lookup
                    ON event_dimensions (key, value, event_id);

                    CREATE TABLE IF NOT EXISTS budgets (
                        scope TEXT NOT NULL,
                        period TEXT NOT NULL CHECK (period IN ('daily', 'monthly')),
                        limit_usd TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (scope, period)
                    );
                    """
                )
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version == 1:
            with connection:
                connection.executescript(
                    """
                    ALTER TABLE model_prices
                    ADD COLUMN cache_write_input_rate TEXT NOT NULL DEFAULT '0';
                    UPDATE model_prices SET cache_write_input_rate = input_rate;

                    ALTER TABLE usage_events
                    ADD COLUMN cache_write_input_tokens INTEGER NOT NULL DEFAULT 0
                        CHECK (cache_write_input_tokens >= 0);
                    ALTER TABLE usage_events
                    ADD COLUMN cache_write_input_rate TEXT NOT NULL DEFAULT '0';
                    ALTER TABLE usage_events
                    ADD COLUMN cache_write_input_cost TEXT NOT NULL DEFAULT '0';

                    CREATE TABLE event_dimensions (
                        event_id TEXT NOT NULL REFERENCES usage_events(event_id) ON DELETE CASCADE,
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        PRIMARY KEY (event_id, key)
                    );
                    CREATE INDEX event_dimensions_lookup
                    ON event_dimensions (key, value, event_id);
                    """
                )
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def set_price(
        self,
        *,
        provider: str,
        model: str,
        input_usd_per_million: DecimalInput,
        output_usd_per_million: DecimalInput,
        cached_input_usd_per_million: Optional[DecimalInput] = None,
        cache_write_input_usd_per_million: Optional[DecimalInput] = None,
        effective_from: Optional[Union[str, datetime]] = None,
    ) -> ModelPrice:
        """Upsert one exact provider/model price version."""

        normalized_provider = validated_text(provider, field="provider")
        normalized_model = validated_text(model, field="model")
        input_rate = validated_decimal(
            input_usd_per_million,
            field="input_usd_per_million",
            maximum=MAX_RATE_USD_PER_MILLION,
        )
        output_rate = validated_decimal(
            output_usd_per_million,
            field="output_usd_per_million",
            maximum=MAX_RATE_USD_PER_MILLION,
        )
        cached_rate = validated_decimal(
            input_rate if cached_input_usd_per_million is None else cached_input_usd_per_million,
            field="cached_input_usd_per_million",
            maximum=MAX_RATE_USD_PER_MILLION,
        )
        cache_write_rate = validated_decimal(
            input_rate
            if cache_write_input_usd_per_million is None
            else cache_write_input_usd_per_million,
            field="cache_write_input_usd_per_million",
            maximum=MAX_RATE_USD_PER_MILLION,
        )
        effective = parse_timestamp(effective_from, field="effective_from")
        price = ModelPrice(
            provider=normalized_provider,
            model=normalized_model,
            input_usd_per_million=input_rate,
            output_usd_per_million=output_rate,
            cached_input_usd_per_million=cached_rate,
            cache_write_input_usd_per_million=cache_write_rate,
            effective_from=effective,
        )
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO model_prices (
                    provider, model, effective_from, input_rate, output_rate,
                    cached_input_rate, cache_write_input_rate, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, model, effective_from) DO UPDATE SET
                    input_rate = excluded.input_rate,
                    output_rate = excluded.output_rate,
                    cached_input_rate = excluded.cached_input_rate,
                    cache_write_input_rate = excluded.cache_write_input_rate,
                    created_at = excluded.created_at
                """,
                (
                    price.provider,
                    price.model,
                    format_timestamp(price.effective_from),
                    decimal_text(price.input_usd_per_million),
                    decimal_text(price.output_usd_per_million),
                    decimal_text(price.cached_input_usd_per_million),
                    decimal_text(price.cache_write_input_usd_per_million),
                    format_timestamp(utc_now()),
                ),
            )
        return price

    def list_prices(self) -> List[ModelPrice]:
        """List all explicit prices in deterministic order."""

        with self._lock:
            rows = self.connection.execute(
                """
                SELECT provider, model, effective_from, input_rate, output_rate,
                       cached_input_rate, cache_write_input_rate
                FROM model_prices
                ORDER BY provider, model, effective_from
                """
            ).fetchall()
        return [self._price_from_row(row) for row in rows]

    def get_price(
        self,
        *,
        provider: str,
        model: str,
        at: Optional[Union[str, datetime]] = None,
    ) -> ModelPrice:
        """Resolve the newest exact price effective at the requested time."""

        normalized_provider = validated_text(provider, field="provider")
        normalized_model = validated_text(model, field="model")
        timestamp = parse_timestamp(at, field="at")
        with self._lock:
            row = self.connection.execute(
                """
                SELECT provider, model, effective_from, input_rate, output_rate,
                       cached_input_rate, cache_write_input_rate
                FROM model_prices
                WHERE provider = ? AND model = ? AND effective_from <= ?
                ORDER BY effective_from DESC
                LIMIT 1
                """,
                (normalized_provider, normalized_model, format_timestamp(timestamp)),
            ).fetchone()
        if row is None:
            raise PriceNotFoundError(
                "no price found for "
                f"{normalized_provider}/{normalized_model} at {format_timestamp(timestamp)}; "
                "add one with `samsarix-cost price set`"
            )
        return self._price_from_row(row)

    @staticmethod
    def _price_from_row(row: sqlite3.Row) -> ModelPrice:
        return ModelPrice(
            provider=row["provider"],
            model=row["model"],
            input_usd_per_million=Decimal(row["input_rate"]),
            output_usd_per_million=Decimal(row["output_rate"]),
            cached_input_usd_per_million=Decimal(row["cached_input_rate"]),
            cache_write_input_usd_per_million=Decimal(row["cache_write_input_rate"]),
            effective_from=parse_timestamp(row["effective_from"], field="effective_from"),
        )

    def estimate(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_input_tokens: int = 0,
        cache_write_input_tokens: int = 0,
        at: Optional[Union[str, datetime]] = None,
    ) -> CostBreakdown:
        """Calculate exact USD cost without recording an event."""

        normalized_input = validated_tokens(input_tokens, field="input_tokens")
        normalized_output = validated_tokens(output_tokens, field="output_tokens")
        normalized_cached = validated_tokens(cached_input_tokens, field="cached_input_tokens")
        normalized_cache_write = validated_tokens(
            cache_write_input_tokens, field="cache_write_input_tokens"
        )
        price = self.get_price(provider=provider, model=model, at=at)
        input_cost = money(Decimal(normalized_input) * price.input_usd_per_million / MILLION)
        output_cost = money(Decimal(normalized_output) * price.output_usd_per_million / MILLION)
        cached_cost = money(
            Decimal(normalized_cached) * price.cached_input_usd_per_million / MILLION
        )
        cache_write_cost = money(
            Decimal(normalized_cache_write) * price.cache_write_input_usd_per_million / MILLION
        )
        return CostBreakdown(
            input_usd=input_cost,
            output_usd=output_cost,
            cached_input_usd=cached_cost,
            cache_write_input_usd=cache_write_cost,
            total_usd=money(input_cost + output_cost + cached_cost + cache_write_cost),
            price=price,
        )

    def record(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_input_tokens: int = 0,
        cache_write_input_tokens: int = 0,
        request_id: Optional[str] = None,
        project: Optional[str] = None,
        dimensions: Optional[Mapping[str, str]] = None,
        occurred_at: Optional[Union[str, datetime]] = None,
    ) -> RecordResult:
        """Cost and atomically record one immutable usage event.

        A repeated request id with the same usage tuple returns the original event.
        Reusing the id for different data raises :class:`DuplicateRequestError`.
        """

        normalized_provider = validated_text(provider, field="provider")
        normalized_model = validated_text(model, field="model")
        normalized_request = (
            validated_text(request_id, field="request_id") if request_id is not None else None
        )
        normalized_project = (
            validated_text(project, field="project", reserved=GLOBAL_SCOPE)
            if project is not None
            else None
        )
        normalized_input = validated_tokens(input_tokens, field="input_tokens")
        normalized_output = validated_tokens(output_tokens, field="output_tokens")
        normalized_cached = validated_tokens(cached_input_tokens, field="cached_input_tokens")
        normalized_cache_write = validated_tokens(
            cache_write_input_tokens, field="cache_write_input_tokens"
        )
        normalized_dimensions = validated_dimensions(dimensions)
        explicit_occurred = occurred_at is not None
        occurred = parse_timestamp(occurred_at, field="occurred_at")

        with self._lock:
            if normalized_request:
                existing = self._find_by_request_id(normalized_request)
                if existing is not None:
                    self._assert_same_request(
                        existing,
                        provider=normalized_provider,
                        model=normalized_model,
                        project=normalized_project,
                        input_tokens=normalized_input,
                        output_tokens=normalized_output,
                        cached_input_tokens=normalized_cached,
                        cache_write_input_tokens=normalized_cache_write,
                        dimensions=normalized_dimensions,
                        occurred_at=occurred if explicit_occurred else None,
                    )
                    return RecordResult(existing, created=False)

            cost = self.estimate(
                provider=normalized_provider,
                model=normalized_model,
                input_tokens=normalized_input,
                output_tokens=normalized_output,
                cached_input_tokens=normalized_cached,
                cache_write_input_tokens=normalized_cache_write,
                at=occurred,
            )
            recorded = utc_now()
            event_id = f"evt_{uuid.uuid4().hex}"
            values = (
                event_id,
                normalized_request,
                format_timestamp(occurred),
                format_timestamp(recorded),
                normalized_provider,
                normalized_model,
                normalized_project,
                normalized_input,
                normalized_output,
                normalized_cached,
                normalized_cache_write,
                decimal_text(cost.price.input_usd_per_million),
                decimal_text(cost.price.output_usd_per_million),
                decimal_text(cost.price.cached_input_usd_per_million),
                decimal_text(cost.price.cache_write_input_usd_per_million),
                format_timestamp(cost.price.effective_from),
                decimal_text(cost.input_usd),
                decimal_text(cost.output_usd),
                decimal_text(cost.cached_input_usd),
                decimal_text(cost.cache_write_input_usd),
                decimal_text(cost.total_usd),
            )
            try:
                with self.connection:
                    self.connection.execute(
                        """
                        INSERT INTO usage_events (
                            event_id, request_id, occurred_at, recorded_at, provider,
                            model, project, input_tokens, output_tokens,
                            cached_input_tokens, cache_write_input_tokens, input_rate,
                            output_rate, cached_input_rate, cache_write_input_rate,
                            price_effective_from, input_cost, output_cost,
                            cached_input_cost, cache_write_input_cost, total_cost
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        values,
                    )
                    if normalized_dimensions:
                        self.connection.executemany(
                            "INSERT INTO event_dimensions (event_id, key, value) VALUES (?, ?, ?)",
                            (
                                (event_id, key, dimension_value)
                                for key, dimension_value in normalized_dimensions
                            ),
                        )
            except sqlite3.IntegrityError:
                if not normalized_request:
                    raise
                existing = self._find_by_request_id(normalized_request)
                if existing is None:
                    raise
                self._assert_same_request(
                    existing,
                    provider=normalized_provider,
                    model=normalized_model,
                    project=normalized_project,
                    input_tokens=normalized_input,
                    output_tokens=normalized_output,
                    cached_input_tokens=normalized_cached,
                    cache_write_input_tokens=normalized_cache_write,
                    dimensions=normalized_dimensions,
                    occurred_at=occurred if explicit_occurred else None,
                )
                return RecordResult(existing, created=False)
        event = UsageEvent(
            event_id=event_id,
            request_id=normalized_request,
            occurred_at=occurred,
            recorded_at=recorded,
            provider=normalized_provider,
            model=normalized_model,
            project=normalized_project,
            input_tokens=normalized_input,
            output_tokens=normalized_output,
            cached_input_tokens=normalized_cached,
            cache_write_input_tokens=normalized_cache_write,
            dimensions=normalized_dimensions,
            cost=cost,
        )
        return RecordResult(event, created=True)

    def record_measurement(
        self,
        measurement: UsageMeasurement,
        *,
        project: Optional[str] = None,
        dimensions: Optional[Mapping[str, str]] = None,
        occurred_at: Optional[Union[str, datetime]] = None,
    ) -> RecordResult:
        """Record normalized provider or telemetry usage with allocation metadata."""

        merged_dimensions = dict(measurement.dimensions)
        if dimensions is not None:
            merged_dimensions.update(dimensions)
        return self.record(
            provider=measurement.provider,
            model=measurement.model,
            input_tokens=measurement.input_tokens,
            output_tokens=measurement.output_tokens,
            cached_input_tokens=measurement.cached_input_tokens,
            cache_write_input_tokens=measurement.cache_write_input_tokens,
            request_id=measurement.request_id,
            project=project,
            dimensions=merged_dimensions,
            occurred_at=occurred_at,
        )

    def _find_by_request_id(self, request_id: str) -> Optional[UsageEvent]:
        row = self.connection.execute(
            "SELECT * FROM usage_events WHERE request_id = ?", (request_id,)
        ).fetchone()
        return self._event_from_row(row) if row is not None else None

    def _assert_same_request(
        self,
        event: UsageEvent,
        *,
        provider: str,
        model: str,
        project: Optional[str],
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int,
        cache_write_input_tokens: int,
        dimensions: Tuple[Tuple[str, str], ...],
        occurred_at: Optional[datetime],
    ) -> None:
        same = (
            event.provider == provider
            and event.model == model
            and event.project == project
            and event.input_tokens == input_tokens
            and event.output_tokens == output_tokens
            and event.cached_input_tokens == cached_input_tokens
            and event.cache_write_input_tokens == cache_write_input_tokens
            and event.dimensions == dimensions
            and (occurred_at is None or event.occurred_at == occurred_at)
        )
        if not same:
            raise DuplicateRequestError(
                f"request_id {event.request_id!r} already belongs to different usage data"
            )

    def _event_from_row(self, row: sqlite3.Row) -> UsageEvent:
        price = ModelPrice(
            provider=row["provider"],
            model=row["model"],
            input_usd_per_million=Decimal(row["input_rate"]),
            output_usd_per_million=Decimal(row["output_rate"]),
            cached_input_usd_per_million=Decimal(row["cached_input_rate"]),
            cache_write_input_usd_per_million=Decimal(row["cache_write_input_rate"]),
            effective_from=parse_timestamp(
                row["price_effective_from"], field="price_effective_from"
            ),
        )
        cost = CostBreakdown(
            input_usd=Decimal(row["input_cost"]),
            output_usd=Decimal(row["output_cost"]),
            cached_input_usd=Decimal(row["cached_input_cost"]),
            cache_write_input_usd=Decimal(row["cache_write_input_cost"]),
            total_usd=Decimal(row["total_cost"]),
            price=price,
        )
        return UsageEvent(
            event_id=row["event_id"],
            request_id=row["request_id"],
            occurred_at=parse_timestamp(row["occurred_at"], field="occurred_at"),
            recorded_at=parse_timestamp(row["recorded_at"], field="recorded_at"),
            provider=row["provider"],
            model=row["model"],
            project=row["project"],
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            cached_input_tokens=int(row["cached_input_tokens"]),
            cache_write_input_tokens=int(row["cache_write_input_tokens"]),
            dimensions=self._dimensions_for_event(row["event_id"]),
            cost=cost,
        )

    def _dimensions_for_event(self, event_id: str) -> Tuple[Tuple[str, str], ...]:
        rows = self.connection.execute(
            "SELECT key, value FROM event_dimensions WHERE event_id = ? ORDER BY key",
            (event_id,),
        ).fetchall()
        return tuple((row["key"], row["value"]) for row in rows)

    def summarize(
        self,
        *,
        since: Optional[Union[str, datetime]] = None,
        until: Optional[Union[str, datetime]] = None,
        project: Optional[str] = None,
        group_by: str = "none",
        dimensions: Optional[Mapping[str, str]] = None,
    ) -> List[SummaryRow]:
        """Stream matching events into exact, deterministic report groups."""

        dimension_group: Optional[str] = None
        if group_by.startswith("dimension:"):
            dimension_group = validated_text(
                group_by.removeprefix("dimension:"), field="group dimension"
            )
        elif group_by not in VALID_GROUPS:
            raise ValidationError(
                f"group_by must be one of {', '.join(VALID_GROUPS)} or dimension:<key>"
            )
        start = parse_timestamp(since, field="since") if since is not None else None
        end = parse_timestamp(until, field="until") if until is not None else None
        if start is not None and end is not None and start >= end:
            raise ValidationError("since must be earlier than until")
        normalized_project = (
            validated_text(project, field="project", reserved=GLOBAL_SCOPE)
            if project is not None
            else None
        )
        normalized_dimensions = validated_dimensions(dimensions)

        clauses: List[str] = []
        where_parameters: List[object] = []
        if start is not None:
            clauses.append("usage_events.occurred_at >= ?")
            where_parameters.append(format_timestamp(start))
        if end is not None:
            clauses.append("usage_events.occurred_at < ?")
            where_parameters.append(format_timestamp(end))
        if normalized_project is not None:
            clauses.append("usage_events.project = ?")
            where_parameters.append(normalized_project)
        for dimension_key, dimension_value in normalized_dimensions:
            clauses.append(
                "EXISTS (SELECT 1 FROM event_dimensions filtered_dimension "
                "WHERE filtered_dimension.event_id = usage_events.event_id "
                "AND filtered_dimension.key = ? AND filtered_dimension.value = ?)"
            )
            where_parameters.extend((dimension_key, dimension_value))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        # Query structure is limited to the fixed clauses above; all values remain bound.
        dimension_select = ""
        select_parameters: List[object] = []
        if dimension_group is not None:
            dimension_select = (
                "(SELECT value FROM event_dimensions grouped_dimension "
                "WHERE grouped_dimension.event_id = usage_events.event_id "
                "AND grouped_dimension.key = ? LIMIT 1) AS dimension_value, "
            )
            select_parameters.append(dimension_group)
        usage_select = (
            f"SELECT {dimension_select}occurred_at, provider, model, project, input_tokens, "
            "output_tokens, cached_input_tokens, cache_write_input_tokens, total_cost "
            "FROM usage_events"
        )
        query = f"{usage_select}{where} ORDER BY occurred_at, event_id"
        parameters = select_parameters + where_parameters

        aggregates: "OrderedDict[str, Tuple[int, int, int, int, int, Decimal]]" = OrderedDict()
        with self._lock:
            cursor = self.connection.execute(query, parameters)
            for row in cursor:
                key = self._group_key(row, group_by)
                current = aggregates.get(key, (0, 0, 0, 0, 0, Decimal("0")))
                aggregates[key] = (
                    current[0] + 1,
                    current[1] + int(row["input_tokens"]),
                    current[2] + int(row["output_tokens"]),
                    current[3] + int(row["cached_input_tokens"]),
                    current[4] + int(row["cache_write_input_tokens"]),
                    current[5] + Decimal(row["total_cost"]),
                )

        return [
            SummaryRow(
                group=key,
                requests=values[0],
                input_tokens=values[1],
                output_tokens=values[2],
                cached_input_tokens=values[3],
                cache_write_input_tokens=values[4],
                total_usd=money(values[5]),
            )
            for key, values in aggregates.items()
        ]

    @staticmethod
    def _group_key(row: sqlite3.Row, group_by: str) -> str:
        if group_by.startswith("dimension:"):
            return (
                str(row["dimension_value"])
                if row["dimension_value"] is not None
                else "(unassigned)"
            )
        if group_by == "provider":
            return str(row["provider"])
        if group_by == "model":
            return f"{row['provider']}/{row['model']}"
        if group_by == "project":
            return str(row["project"]) if row["project"] is not None else "(unassigned)"
        if group_by in ("day", "month"):
            occurred = parse_timestamp(row["occurred_at"], field="occurred_at")
            return occurred.strftime("%Y-%m-%d" if group_by == "day" else "%Y-%m")
        return "all"

    def set_budget(
        self,
        *,
        limit_usd: DecimalInput,
        period: str,
        project: Optional[str] = None,
    ) -> None:
        """Set or replace a global or per-project budget."""

        if period not in VALID_PERIODS:
            raise ValidationError(f"period must be one of {', '.join(VALID_PERIODS)}")
        limit = validated_decimal(
            limit_usd,
            field="limit_usd",
            maximum=MAX_RATE_USD_PER_MILLION,
            allow_zero=False,
        )
        scope = (
            validated_text(project, field="project", reserved=GLOBAL_SCOPE)
            if project is not None
            else GLOBAL_SCOPE
        )
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO budgets (scope, period, limit_usd, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scope, period) DO UPDATE SET
                    limit_usd = excluded.limit_usd,
                    updated_at = excluded.updated_at
                """,
                (scope, period, decimal_text(limit), format_timestamp(utc_now())),
            )

    def check_budgets(
        self,
        *,
        estimated_usd: DecimalInput = Decimal("0"),
        project: Optional[str] = None,
        at: Optional[Union[str, datetime]] = None,
    ) -> List[BudgetStatus]:
        """Evaluate all applicable global and project budgets."""

        estimated = validated_decimal(estimated_usd, field="estimated_usd")
        normalized_project = (
            validated_text(project, field="project", reserved=GLOBAL_SCOPE)
            if project is not None
            else None
        )
        timestamp = parse_timestamp(at, field="at")
        scopes = [GLOBAL_SCOPE]
        if normalized_project is not None:
            scopes.append(normalized_project)
        with self._lock:
            if normalized_project is None:
                budget_rows = self.connection.execute(
                    "SELECT scope, period, limit_usd FROM budgets "
                    "WHERE scope = ? ORDER BY scope, period",
                    scopes,
                ).fetchall()
            else:
                budget_rows = self.connection.execute(
                    "SELECT scope, period, limit_usd FROM budgets "
                    "WHERE scope IN (?, ?) ORDER BY scope, period",
                    scopes,
                ).fetchall()

        statuses: List[BudgetStatus] = []
        for row in budget_rows:
            period_start, period_end = self._period_bounds(timestamp, row["period"])
            scope_project = None if row["scope"] == GLOBAL_SCOPE else row["scope"]
            summaries = self.summarize(
                since=period_start,
                until=period_end,
                project=scope_project,
                group_by="none",
            )
            spent = summaries[0].total_usd if summaries else Decimal("0")
            limit = Decimal(row["limit_usd"])
            projected = money(spent + estimated)
            statuses.append(
                BudgetStatus(
                    scope="global" if row["scope"] == GLOBAL_SCOPE else row["scope"],
                    period=row["period"],
                    period_start=period_start,
                    limit_usd=limit,
                    spent_usd=money(spent),
                    estimated_usd=money(estimated),
                    projected_usd=projected,
                    remaining_usd=money(max(Decimal("0"), limit - projected)),
                    allowed=projected <= limit,
                )
            )
        return statuses

    @staticmethod
    def _period_bounds(timestamp: datetime, period: str) -> Tuple[datetime, Optional[datetime]]:
        normalized = timestamp.astimezone(timezone.utc)
        if period == "daily":
            start = normalized.replace(hour=0, minute=0, second=0, microsecond=0)
            try:
                return start, start + timedelta(days=1)
            except OverflowError:
                return start, None
        start = normalized.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            try:
                end = start.replace(year=start.year + 1, month=1)
            except ValueError:
                end = None
        else:
            end = start.replace(month=start.month + 1)
        return start, end
