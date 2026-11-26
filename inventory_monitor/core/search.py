"""Keyword, category, status and price filtering with sorting."""
from __future__ import annotations

from typing import Iterable

from .models import Product

SORT_KEYS = {
    "name": lambda p: p.name.lower(),
    "sku": lambda p: p.sku,
    "category": lambda p: (p.category.lower(), p.name.lower()),
    "quantity": lambda p: p.quantity,
    "unit_price": lambda p: p.unit_price,
    "stock_value": lambda p: p.stock_value,
    "status": lambda p: (("OUT", "LOW", "OK").index(p.status.value), p.name.lower()),
}


def search(products: Iterable[Product], keyword: str = "", category: str = "",
           status: str | Iterable[str] = "", min_price: float | None = None,
           max_price: float | None = None, sort_by: str = "name",
           descending: bool = False) -> list[Product]:
    """All filters combine with AND. Every keyword token must appear in
    SKU, name, category or supplier (case-insensitive)."""
    tokens = keyword.lower().split()
    if isinstance(status, str):
        statuses = {status.upper()} if status else set()
    else:
        statuses = {s.upper() for s in status}
    cat = category.strip().lower()

    results = []
    for p in products:
        if cat and p.category.lower() != cat:
            continue
        if statuses and p.status.value not in statuses:
            continue
        if min_price is not None and p.unit_price < min_price:
            continue
        if max_price is not None and p.unit_price > max_price:
            continue
        if tokens:
            haystack = f"{p.sku} {p.name} {p.category} {p.supplier}".lower()
            if not all(t in haystack for t in tokens):
                continue
        results.append(p)

    key = SORT_KEYS.get(sort_by, SORT_KEYS["name"])
    return sorted(results, key=key, reverse=descending)
