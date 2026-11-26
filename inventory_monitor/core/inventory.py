"""Business logic: add, update, sell, restock, adjust, delete, plus a movement ledger."""
from __future__ import annotations

import difflib
from contextlib import contextmanager
from datetime import date, datetime
from typing import Callable

from .models import Movement, Product, StockStatus
from .storage import Storage
from .validation import (
    MAX_QTY, ValidationError, validate_positive_quantity, validate_quantity,
    validate_record, validate_sku,
)

EDITABLE_FIELDS = {"name", "category", "unit_price", "reorder_level", "supplier"}


class InventoryError(Exception):
    """A business-rule failure (unknown SKU, duplicate SKU, not enough stock...)."""


class Inventory:
    def __init__(self, storage: Storage, clock: Callable[[], datetime] = datetime.now):
        self.storage = storage
        self._clock = clock
        self.autosave = True
        products, movements, self.notes = storage.load()
        self.products: dict[str, Product] = {p.sku: p for p in products}
        self.movements: list[Movement] = movements

    # ------------------------------------------------------------- basics
    @property
    def today(self) -> date:
        return self._clock().date()

    def all(self) -> list[Product]:
        return list(self.products.values())

    def categories(self) -> list[str]:
        return sorted({p.category for p in self.products.values()}, key=str.lower)

    def get(self, sku: str) -> Product:
        try:
            key = validate_sku(sku)
        except ValidationError:
            key = str(sku).strip().upper()
        if key not in self.products:
            close = difflib.get_close_matches(key, list(self.products), n=3, cutoff=0.5)
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            raise InventoryError(f"No product with SKU {key!r}.{hint}")
        return self.products[key]

    def save(self) -> None:
        self.storage.save(self.all(), self.movements)

    def _persist(self) -> None:
        if self.autosave:
            self.save()

    @contextmanager
    def batch(self):
        """Defer saving until the block ends (used by bulk import)."""
        previous, self.autosave = self.autosave, False
        try:
            yield self
        finally:
            self.autosave = previous
            self.save()

    def _log(self, sku: str, kind: str, delta: int, resulting: int, note: str = "") -> None:
        self.movements.append(Movement(
            timestamp=self._clock().isoformat(timespec="seconds"),
            sku=sku, kind=kind, delta=delta, resulting_qty=resulting, note=note.strip()))

    def _canon_category(self, category: str) -> str:
        for existing in self.categories():
            if existing.lower() == category.lower():
                return existing
        return category

    # ----------------------------------------------------------- mutations
    def add(self, raw: dict, kind: str = "initial", note: str = "Product created") -> Product:
        clean, errors = validate_record(raw, self.today)
        if errors:
            raise ValidationError("; ".join(errors))
        if clean["sku"] in self.products:
            raise InventoryError(f"SKU {clean['sku']} already exists.")
        stamp = self.today.isoformat()
        clean["created_at"] = clean["created_at"] or stamp
        clean["last_restocked"] = clean["last_restocked"] or stamp
        clean["category"] = self._canon_category(clean["category"])
        product = Product.from_dict(clean)
        self.products[product.sku] = product
        self._log(product.sku, kind, product.quantity, product.quantity, note)
        self._persist()
        return product

    def update(self, sku: str, changes: dict) -> Product:
        product = self.get(sku)
        unknown = set(changes) - EDITABLE_FIELDS
        if unknown:
            raise InventoryError(
                f"Cannot edit {', '.join(sorted(unknown))} here. Use sell/restock/adjust for quantity.")
        clean, errors = validate_record(changes, self.today, partial=True)
        if errors:
            raise ValidationError("; ".join(errors))
        if "category" in clean:
            clean["category"] = self._canon_category(clean["category"])
        for key, value in clean.items():
            setattr(product, key, value)
        self._persist()
        return product

    def remove(self, sku: str) -> Product:
        product = self.get(sku)
        del self.products[product.sku]
        self._log(product.sku, "adjustment", -product.quantity, 0, "Product deleted")
        self._persist()
        return product

    def sell(self, sku: str, qty, note: str = "") -> Product:
        product = self.get(sku)
        n = validate_positive_quantity(qty)
        if n > product.quantity:
            raise InventoryError(
                f"Only {product.quantity} of {product.sku} in stock; cannot sell {n}.")
        product.quantity -= n
        self._log(product.sku, "sale", -n, product.quantity, note or "Sale")
        self._persist()
        return product

    def restock(self, sku: str, qty, note: str = "") -> Product:
        product = self.get(sku)
        n = validate_positive_quantity(qty)
        if product.quantity + n > MAX_QTY:
            raise InventoryError(
                f"Restocking {n:,} would exceed the maximum of {MAX_QTY:,} units for {product.sku}.")
        product.quantity += n
        product.last_restocked = self.today.isoformat()
        self._log(product.sku, "restock", n, product.quantity, note or "Restock")
        self._persist()
        return product

    def set_quantity(self, sku: str, new_qty, note: str = "") -> Product:
        product = self.get(sku)
        n = validate_quantity(new_qty)
        if n == product.quantity:
            raise InventoryError(f"{product.sku} already has quantity {n}; nothing to change.")
        delta = n - product.quantity
        product.quantity = n
        self._log(product.sku, "adjustment", delta, n, note or "Manual adjustment")
        self._persist()
        return product

    # -------------------------------------------------------------- queries
    def low_stock(self) -> list[Product]:
        return sorted((p for p in self.products.values() if p.status is StockStatus.LOW),
                      key=lambda p: p.quantity / max(p.reorder_level, 1))

    def out_of_stock(self) -> list[Product]:
        return [p for p in self.products.values() if p.status is StockStatus.OUT]

    def history(self, sku: str | None = None, limit: int | None = None) -> list[Movement]:
        items = [m for m in self.movements if sku is None or m.sku == sku.strip().upper()]
        items = list(reversed(sorted(items, key=lambda m: m.timestamp)))   # stable: ties keep newest-appended first
        return items[:limit] if limit else items
