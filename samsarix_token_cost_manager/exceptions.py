# Copyright 2026 Samsarix LLC
# SPDX-License-Identifier: Apache-2.0

"""Public exception hierarchy for the token cost manager."""


class CostManagerError(Exception):
    """Base class for expected product errors."""


class ValidationError(CostManagerError, ValueError):
    """Raised when user-supplied data is invalid."""


class PriceNotFoundError(CostManagerError, LookupError):
    """Raised when no effective price exists for a model usage event."""


class DuplicateRequestError(CostManagerError):
    """Raised when a request id is reused for different usage data."""
