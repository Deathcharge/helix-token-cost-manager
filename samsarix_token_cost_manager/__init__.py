# Copyright 2026 Samsarix LLC
# SPDX-License-Identifier: Apache-2.0

"""Local-first, provider-neutral token usage and cost accounting."""

from .adapters import (
    UsageMeasurement,
    from_anthropic_response,
    from_openai_response,
    from_otel_attributes,
)
from .exceptions import (
    CostManagerError,
    DuplicateRequestError,
    PriceNotFoundError,
    ValidationError,
)
from .manager import CostManager, default_database_path
from .models import (
    BudgetStatus,
    CostBreakdown,
    ModelPrice,
    RecordResult,
    SummaryRow,
    UsageEvent,
)

__all__ = [
    "BudgetStatus",
    "CostBreakdown",
    "CostManager",
    "CostManagerError",
    "DuplicateRequestError",
    "ModelPrice",
    "PriceNotFoundError",
    "RecordResult",
    "SummaryRow",
    "UsageEvent",
    "UsageMeasurement",
    "ValidationError",
    "default_database_path",
    "from_anthropic_response",
    "from_openai_response",
    "from_otel_attributes",
]

__version__ = "0.1.0"
