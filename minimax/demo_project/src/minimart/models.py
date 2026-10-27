"""Order model shared by the repository and the service layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

VALID_STATUSES = ("draft", "paid", "shipped", "cancelled")


@dataclass(frozen=True)
class Order:
    """One customer order. Money is stored in cents to keep comparisons exact."""

    order_id: str
    customer: str
    status: str
    total_cents: int
    created_at: datetime

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"unknown order status: {self.status!r}")
        if self.total_cents < 0:
            raise ValueError("total_cents must be non-negative")
