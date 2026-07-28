"""Local-first, provider-neutral token usage and cost accounting."""

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
    "ValidationError",
    "default_database_path",
]

__version__ = "0.1.0"
