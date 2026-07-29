# Copyright 2026 Samsarix LLC
# SPDX-License-Identifier: Apache-2.0

"""Complete local example using illustrative, non-provider pricing."""

from pathlib import Path
from tempfile import TemporaryDirectory

from samsarix_token_cost_manager import CostManager


def main() -> None:
    """Configure a price, record usage, and print a monthly summary."""

    with TemporaryDirectory() as directory:
        database = Path(directory) / "costs.sqlite3"
        with CostManager(database) as costs:
            costs.set_price(
                provider="example",
                model="model-v1",
                input_usd_per_million="2.50",
                output_usd_per_million="10.00",
                cached_input_usd_per_million="0.25",
                effective_from="2026-01-01",
            )
            event = costs.record(
                provider="example",
                model="model-v1",
                input_tokens=1_000_000,
                output_tokens=500_000,
                cached_input_tokens=100_000,
                project="demo",
                request_id="example-request-1",
                occurred_at="2026-07-28T12:00:00Z",
            ).event
            print(f"Event cost: ${event.cost.total_usd}")
            for row in costs.summarize(since="2026-07-01", until="2026-08-01", group_by="project"):
                print(row.to_dict())


if __name__ == "__main__":
    main()
