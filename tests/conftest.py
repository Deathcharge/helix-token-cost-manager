# Copyright 2026 Samsarix LLC
# SPDX-License-Identifier: Apache-2.0

"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from samsarix_token_cost_manager import CostManager


@pytest.fixture
def manager(tmp_path: Path) -> CostManager:
    """Return a clean manager with deterministic example pricing."""

    with CostManager(tmp_path / "costs.sqlite3") as cost_manager:
        cost_manager.set_price(
            provider="example",
            model="model-v1",
            input_usd_per_million="2.50",
            output_usd_per_million="10",
            cached_input_usd_per_million="0.25",
            effective_from="2026-01-01",
        )
        yield cost_manager
