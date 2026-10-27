"""Shared fixtures: six orders with controlled timestamps, totals and statuses."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimart import Order, OrderRepository, OrderService  # noqa: E402


@pytest.fixture()
def orders() -> list[Order]:
    return [
        Order("O-1001", "alice", "paid", 5000, datetime(2026, 10, 1, 10, 0)),
        Order("O-1002", "bob", "draft", 1200, datetime(2026, 10, 2, 10, 0)),
        Order("O-1003", "carol", "paid", 8000, datetime(2026, 10, 3, 10, 0)),
        Order("O-1004", "dave", "shipped", 5000, datetime(2026, 10, 4, 10, 0)),
        Order("O-1005", "erin", "paid", 3000, datetime(2026, 10, 5, 10, 0)),
        Order("O-1006", "frank", "cancelled", 9000, datetime(2026, 10, 6, 10, 0)),
    ]


@pytest.fixture()
def service(orders: list[Order]) -> OrderService:
    return OrderService(OrderRepository(orders))
