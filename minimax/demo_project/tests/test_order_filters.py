"""Target functional tests for REQ-001 (order query filters).

Written before the implementation: all tests in this module fail on the base
commit because ``list_orders`` raises ``NotImplementedError`` when a filter is
requested, and pass once REQ-001 is implemented by MiniMax.
"""

from __future__ import annotations

import pytest

from minimart import ValidationError


def test_filter_by_status(service):
    """@req REQ-001.1"""
    result = service.list_orders(status="paid")
    assert [order.order_id for order in result] == ["O-1005", "O-1003", "O-1001"]


def test_blank_status_is_ignored(service):
    """@req REQ-001.1"""
    result = service.list_orders(status="   ")
    assert len(result) == 6


def test_unknown_status_raises(service):
    """@req REQ-001.1"""
    with pytest.raises(ValidationError):
        service.list_orders(status="refunded")


def test_min_total_is_inclusive(service):
    """@req REQ-001.2"""
    result = service.list_orders(min_total=5000)
    assert [order.order_id for order in result] == [
        "O-1006",
        "O-1004",
        "O-1003",
        "O-1001",
    ]


def test_min_total_zero_is_ignored(service):
    """@req REQ-001.2"""
    assert len(service.list_orders(min_total=0)) == 6


def test_negative_min_total_raises(service):
    """@req REQ-001.2"""
    with pytest.raises(ValidationError):
        service.list_orders(min_total=-1)


def test_filters_combine_before_pagination(service):
    """@req REQ-001.3"""
    result = service.list_orders(status="paid", page=2, page_size=2)
    assert [order.order_id for order in result] == ["O-1001"]


def test_filters_keep_stable_order(service):
    """@req REQ-001.4"""
    result = service.list_orders(status="paid", min_total=3000)
    assert [order.order_id for order in result] == ["O-1005", "O-1003", "O-1001"]


def test_filters_do_not_mutate_repository(service, orders):
    """@req REQ-001.5"""
    before = [order.order_id for order in service.list_orders()]
    service.list_orders(status="shipped", min_total=1000)
    after = [order.order_id for order in service.list_orders()]
    assert before == after
    assert len(orders) == 6
