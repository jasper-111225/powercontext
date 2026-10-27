"""Regression tests for existing behaviour (specs/REQ-002-pagination.md).

These tests pass before and after the REQ-001 change; they are the compatibility
guard for the MiniMax code modification.
"""

from __future__ import annotations

import pytest

from minimart import ValidationError


def test_lists_all_orders_newest_first(service):
    """@req REQ-002.1"""
    result = service.list_orders()
    assert [order.order_id for order in result] == [
        "O-1006",
        "O-1005",
        "O-1004",
        "O-1003",
        "O-1002",
        "O-1001",
    ]


def test_pagination_returns_requested_slice(service):
    """@req REQ-002.1"""
    result = service.list_orders(page=2, page_size=2)
    assert [order.order_id for order in result] == ["O-1004", "O-1003"]


def test_page_beyond_last_is_empty(service):
    """@req REQ-002.1"""
    assert service.list_orders(page=9, page_size=10) == []


def test_invalid_page_raises(service):
    """@req REQ-002.2"""
    with pytest.raises(ValidationError):
        service.list_orders(page=0)


def test_invalid_page_size_raises(service):
    """@req REQ-002.2"""
    with pytest.raises(ValidationError):
        service.list_orders(page_size=101)


def test_summary_counts_and_totals(service):
    """@req REQ-002.3"""
    assert service.get_summary() == {"count": 6, "total_cents": 31200}
