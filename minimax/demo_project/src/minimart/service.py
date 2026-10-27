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

    # @req REQ-002.1, REQ-002.2
    def list_orders(
        self,
        page: int = 1,
        page_size: int = 10,
        status: str | None = None,
        min_total: int | None = None,
    ) -> list[Order]:
        """Return one page of orders, newest first.

        ``status`` and ``min_total`` filtering is requested by REQ-001 and is
        not implemented yet: the parameters are accepted so callers can adopt
        the documented signature before the behaviour lands.
        """
        self._validate_paging(page, page_size)
        if status is not None or min_total is not None:
            # TODO(REQ-001.1, REQ-001.2): filter by status and by the inclusive
            # min_total bound.
            raise NotImplementedError(
                "status/min_total filtering is not implemented yet (REQ-001)"
            )

        orders = self._sorted_orders()
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
