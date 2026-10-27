"""Order query service.

Implements the behaviour required by ``specs/REQ-001-order-query.md`` and
``specs/REQ-002-pagination.md``. Requirement markers (``@req``) are read by the
SDD context module to link code symbols to specification entries.
"""

from __future__ import annotations

from .errors import ValidationError
from .models import VALID_STATUSES, Order
from .repository import OrderRepository

MAX_PAGE_SIZE = 100


class OrderService:
    def __init__(self, repository: OrderRepository) -> None:
        self._repository = repository

    # @req REQ-002.1, REQ-002.2, REQ-001.1, REQ-001.2, REQ-001.3
    def list_orders(
        self,
        page: int = 1,
        page_size: int = 10,
        status: str | None = None,
        min_total: int | None = None,
    ) -> list[Order]:
        """Return one page of orders, newest first.

        Blank ``status`` values and a zero ``min_total`` disable their respective
        filters. Filters are applied before pagination.
        """
        self._validate_paging(page, page_size)
        normalized_status = self._normalize_status(status)
        minimum_total = self._normalize_min_total(min_total)

        orders = self._sorted_orders()
        if normalized_status is not None:
            orders = [
                order for order in orders if order.status == normalized_status
            ]
        if minimum_total is not None:
            orders = [
                order for order in orders if order.total_cents >= minimum_total
            ]

        start = (page - 1) * page_size
        return orders[start : start + page_size]

    # @req REQ-002.3
    def get_summary(self) -> dict[str, int]:
        orders = self._repository.all_orders()
        return {
            "count": len(orders),
            "total_cents": sum(order.total_cents for order in orders),
        }

    def _sorted_orders(self) -> list[Order]:
        # @req REQ-001.4
        return sorted(
            self._repository.all_orders(),
            key=lambda order: (order.created_at, order.order_id),
            reverse=True,
        )

    def _normalize_status(self, status: str | None) -> str | None:
        # @req REQ-001.1
        if status is None:
            return None
        if not isinstance(status, str):
            raise ValidationError(
                "status must be one of: draft, paid, shipped, cancelled"
            )
        if not status.strip():
            return None
        if not status_is_valid(status):
            raise ValidationError(
                "status must be one of: draft, paid, shipped, cancelled"
            )
        return status

    def _normalize_min_total(self, min_total: int | None) -> int | None:
        # @req REQ-001.2
        if min_total is None:
            return None
        if (
            not isinstance(min_total, int)
            or isinstance(min_total, bool)
            or min_total < 0
        ):
            raise ValidationError("min_total must be an integer >= 0")
        if min_total == 0:
            return None
        return min_total

    def _validate_paging(self, page: int, page_size: int) -> None:
        # @req REQ-002.2
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ValidationError("page must be an integer >= 1")
        if (
            not isinstance(page_size, int)
            or isinstance(page_size, bool)
            or page_size < 1
            or page_size > MAX_PAGE_SIZE
        ):
            raise ValidationError(f"page_size must be an integer in [1, {MAX_PAGE_SIZE}]")


def status_is_valid(status: str) -> bool:
    """Return True when ``status`` is one of the documented order statuses."""
    return status in VALID_STATUSES
