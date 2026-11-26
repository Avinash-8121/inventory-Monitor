"""Data structures: Product, Movement and StockStatus."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum


class StockStatus(str, Enum):
    OK = "OK"
    LOW = "LOW"
    OUT = "OUT"


PRODUCT_FIELDS = [
    "sku", "name", "category", "quantity", "unit_price",
    "reorder_level", "supplier", "last_restocked", "created_at",
]


@dataclass
class Product:
    sku: str
    name: str
    category: str
    quantity: int
    unit_price: float
    reorder_level: int
    supplier: str = ""
    last_restocked: str = ""   # ISO date, YYYY-MM-DD
    created_at: str = ""       # ISO date, YYYY-MM-DD

    @property
    def status(self) -> StockStatus:
        """OUT at zero, LOW at or below the reorder level, otherwise OK."""
        if self.quantity <= 0:
            return StockStatus.OUT
        if self.quantity <= self.reorder_level:
            return StockStatus.LOW
        return StockStatus.OK

    @property
    def stock_value(self) -> float:
        return round(self.quantity * self.unit_price, 2)

    def days_since_restock(self, today: date) -> int | None:
        if not self.last_restocked:
            return None
        return (today - date.fromisoformat(self.last_restocked)).days

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Product":
        return cls(**{k: data[k] for k in PRODUCT_FIELDS})


@dataclass
class Movement:
    """One stock-ledger entry. kind: sale | restock | adjustment | initial | import."""
    timestamp: str
    sku: str
    kind: str
    delta: int
    resulting_qty: int
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Movement":
        return cls(
            timestamp=str(data["timestamp"]), sku=str(data["sku"]),
            kind=str(data["kind"]), delta=int(data["delta"]),
            resulting_qty=int(data["resulting_qty"]), note=str(data.get("note", "")),
        )
