"""In-memory order repository used by the demo service and its tests."""

from __future__ import annotations

from typing import Iterable

from .models import Order


class OrderRepository:
    """Keeps an immutable snapshot of orders so tests can assert no mutation."""

    def __init__(self, orders: Iterable[Order] = ()) -> None:
        self._orders: tuple[Order, ...] = tuple(orders)

    def all_orders(self) -> tuple[Order, ...]:
        return self._orders

    def add_many(self, orders: Iterable[Order]) -> None:
        self._orders = self._orders + tuple(orders)

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._orders)
