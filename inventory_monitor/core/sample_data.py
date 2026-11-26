"""Seed realistic demo data: 40 products (normal, boundary and edge cases) with movement history.

Run:  python -m core.sample_data            (rewrites data/ files, resets the live inventory)
"""
from __future__ import annotations

import csv
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import PRODUCT_FIELDS, Movement, Product
from .storage import SCHEMA_VERSION, Storage
from .validation import validate_record

LONG_NAME = "Reusable Tote Bag - Extra Long Product Name Used To Stress Test Table Layouts 80"
assert len(LONG_NAME) == 80

# sku, name, category, qty, price, reorder, supplier, days since restock, case label
_ROWS = [
    ("STN-001", "A4 Ruled Notebook (200 pages)", "Stationery", 120, 3.50, 40, "PaperCo", 12),
    ("STN-002", "Blue Gel Pens (Pack of 10)", "Stationery", 85, 4.25, 30, "PaperCo", 20),
    ("STN-003", "HB Pencils (Box of 12)", "Stationery", 28, 2.10, 30, "PaperCo", 45),            # LOW
    ("STN-004", "Highlighter Set (6 colours)", "Stationery", 0, 5.99, 15, "BrightWrite", 75),    # OUT
    ("STN-005", "Sticky Notes 3x3 (Pack of 12)", "Stationery", 60, 3.75, 20, "BrightWrite", 8),
    ("STN-006", "Stapler with 1000 Staples", "Stationery", 15, 6.40, 15, "OfficePro", 30),       # boundary: qty == reorder
    ("STN-007", "Geometry Box", "Stationery", 42, 4.80, 10, "OfficePro", 60),
    ("STN-008", "Ballpoint Pens Bulk Case (1000 ct)", "Stationery", 25000, 0.15, 500, "PaperCo", 5),  # anomaly: huge qty
    ("ELC-001", "USB-C Charging Cable 1m", "Electronics", 64, 7.99, 25, "VoltEdge", 14),
    ("ELC-002", "Wireless Mouse", "Electronics", 22, 12.50, 12, "VoltEdge", 22),
    ("ELC-003", "Scientific Calculator FX-991", "Electronics", 9, 24.90, 10, "CalcWorld", 50),   # LOW
    ("ELC-004", "Bluetooth Earbuds", "Electronics", 2, 29.99, 10, "SoundHive", 95),              # critical + overdue
    ("ELC-005", "Laptop 14in Student Edition", "Electronics", 3, 1499.99, 2, "NovaTech", 40),   # anomaly: high price
    ("ELC-006", "Power Bank 10000mAh", "Electronics", 18, 19.99, 10, "VoltEdge", 18),
    ("ELC-007", "USB Flash Drive 64GB", "Electronics", 0, 8.99, 20, "DataStor", 33),             # OUT
    ("SNK-001", "Instant Noodles Cup", "Snacks & Beverages", 200, 0.99, 80, "CampusFoods", 4),
    ("SNK-002", "Cold Coffee Can 250ml", "Snacks & Beverages", 96, 1.75, 48, "CampusFoods", 6),
    ("SNK-003", "Granola Bar Box (12)", "Snacks & Beverages", 31, 6.50, 20, "HealthyBite", 15),
    ("SNK-004", "Mineral Water 1L", "Snacks & Beverages", 150, 0.60, 100, "CampusFoods", 3),
    ("SNK-005", "Potato Chips Family Pack", "Snacks & Beverages", 40, 2.20, 40, "HealthyBite", 9),   # boundary: LOW
    ("SNK-006", "Chocolate Bar Assorted", "Snacks & Beverages", 41, 1.10, 40, "HealthyBite", 9),     # boundary: OK
    ("DRM-001", "LED Desk Lamp", "Dorm Essentials", 17, 15.99, 8, "BrightHome", 28),
    ("DRM-002", "Extension Board 4-Socket", "Dorm Essentials", 12, 11.50, 10, "VoltEdge", 36),
    ("DRM-003", "Laundry Bag", "Dorm Essentials", 33, 5.25, 10, "HomeNest", 70),
    ("DRM-004", "Foldable Study Table", "Dorm Essentials", 6, 39.00, 5, "HomeNest", 55),
    ("DRM-005", "Bedsheet Set Single", "Dorm Essentials", 14, 22.00, 8, "HomeNest", 200),         # stale restock
    ("DRM-006", "Water Bottle 750ml Steel", "Dorm Essentials", 0, 9.75, 12, "HomeNest", 120),      # OUT + overdue
    ("LAB-001", "Lab Coat (M)", "Lab Supplies", 10, 14.50, 6, "SciWear", 25),
    ("LAB-002", "Safety Goggles", "Lab Supplies", 8, 6.90, 8, "SciWear", 25),                     # boundary: LOW
    ("LAB-003", "Nitrile Gloves Box (100)", "Lab Supplies", 22, 9.40, 15, "SciWear", 11),
    ("LAB-004", "Graph Paper Pad A4", "Lab Supplies", 54, 2.80, 20, "PaperCo", 17),
    ("LAB-005", "Disposable Pipettes (Pack of 50)", "Lab Supplies", 7, 8.25, 10, "SciWear", 85),  # LOW
    ("BKP-001", "Printing Credits (100 pages)", "Books & Printing", 500, 2.00, 100, "", 2),        # blank supplier
    ("BKP-002", "Spiral Binding Service Token", "Books & Printing", 75, 1.50, 25, "PrintHub", 13),
    ("BKP-003", "Course Reader: Intro to Economics", "Books & Printing", 11, 18.00, 15, "Campus Press", 40),
    ("APP-001", "University Hoodie (L)", "Apparel", 19, 34.00, 10, "CampusWear", 23),
    ("CLN-001", "Hand Sanitizer 250ml", "Cleaning", 48, 3.40, 30, "CleanCo", 10),
    ("CLN-002", LONG_NAME, "Cleaning", 36, 6.00, 12, "CleanCo", 26),                               # 80-char name
    ("CLN-003", "Free Sample Pack (Promo)", "Cleaning", 300, 0.00, 0, "CleanCo", 7),               # zero price, reorder 0
    ("BKP-004", "Exam Answer Booklet (Pack of 20)", "Books & Printing", 90, 3.00, 30, "Campus Press", 19),
]


def build_sample(today: date | None = None, seed: int = 82) -> tuple[list[Product], list[Movement]]:
    today = today or date.today()
    rng = random.Random(seed)
    products, movements = [], []
    for sku, name, cat, qty, price, reorder, supplier, days in _ROWS:
        clean, errors = validate_record({
            "sku": sku, "name": name, "category": cat, "quantity": qty, "unit_price": price,
            "reorder_level": reorder, "supplier": supplier,
            "last_restocked": (today - timedelta(days=days)).isoformat(),
            "created_at": (today - timedelta(days=max(days, 60) + 30)).isoformat(),
        }, today)
        assert not errors, (sku, errors)
        products.append(Product.from_dict(clean))
        movements += _history(rng, clean["sku"], clean["quantity"], today)
    movements.sort(key=lambda m: m.timestamp)
    return products, movements


def _history(rng: random.Random, sku: str, current_qty: int, today: date) -> list[Movement]:
    """Walk backwards from today's quantity so the ledger always reconciles."""
    events, q = [], current_qty
    days_ago = sorted(rng.sample(range(0, 58), rng.randint(3, 7)))   # newest first
    for d in days_ago:
        ts = datetime.combine(today - timedelta(days=d), datetime.min.time()).replace(
            hour=rng.randint(9, 19), minute=rng.randint(0, 59)).isoformat(timespec="seconds")
        size = rng.randint(1, max(2, min(15, max(current_qty, 20) // 8)))
        if rng.random() < 0.28 and q >= size:
            events.append(Movement(ts, sku, "restock", size, q, "Supplier delivery"))
            q -= size
        else:
            events.append(Movement(ts, sku, "sale", -size, q, "Counter sale"))
            q += size
    return events[::-1]


def legacy_csv_text() -> str:
    """A deliberately messy file for the bulk-import demo (valid rows plus every kind of bad row)."""
    return (
        "Item Code, Product Name, Category, Qty, Price, Reorder Level, Vendor\n"
        "NEW-001, Whiteboard Markers (Pack of 4), Stationery, 45, $6.50, 15, BrightWrite\n"
        "new-002, Desk Organiser, Dorm Essentials, 12, 14.99, 6, HomeNest\n"
        "NEW-003, Lab Notebook Hardcover, Lab Supplies, \"1,200\", \"3.25\", 300, SciWear\n"
        "NEW-004, Energy Drink 330ml, Snacks & Beverages, 0, 1.99, 24, CampusFoods\n"
        "NEW-005, Reading Glasses Cleaner, Cleaning, 30, 2.50, 10,\n"
        "NEW-006, Mystery Item, Stationery, -5, 2.00, 10, PaperCo\n"
        "NEW-007, Broken Quantity, Stationery, twelve, 2.00, 10, PaperCo\n"
        "NEW-008, , Stationery, 10, 2.00, 10, PaperCo\n"
        "??, Bad SKU Item, Stationery, 10, 2.00, 10, PaperCo\n"
        "NEW-001, Duplicate Of First Row, Stationery, 9, 2.00, 10, PaperCo\n"
        "STN-001, A4 Ruled Notebook (200 pages), Stationery, 999, 3.50, 40, PaperCo\n"
        "NEW-009, Three Decimal Price, Stationery, 10, 2.999, 10, PaperCo\n"
        "NEW-010, Too Many Columns, Stationery, 10, 2.00, 10, PaperCo, extra, columns\n"
        "\n"
        "NEW-011, Negative Reorder, Stationery, 10, 2.00, -3, PaperCo\n"
        "NEW-012, Fractional Quantity, Stationery, 5.5, 2.00, 10, PaperCo\n"
        "NEW-013, , , 10, 2.00, 10, PaperCo\n"
        "NEW-014, Absurd Quantity, Stationery, 99999999, 2.00, 10, PaperCo\n"
        "NEW-015, USB Hub 4-Port, Electronics, 14, 16.00, 8, VoltEdge\n"
    )


def write_sample_files(data_dir: str | Path = "data", reset_live: bool = True,
                       today: date | None = None) -> dict[str, Path]:
    data = Path(data_dir)
    data.mkdir(parents=True, exist_ok=True)
    products, movements = build_sample(today)

    json_path = data / "sample_inventory.json"
    json_path.write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "products": [p.to_dict() for p in products],
        "movements": [m.to_dict() for m in movements],
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    csv_path = data / "sample_inventory.csv"
    Storage.write_csv(products, csv_path)

    legacy = data / "legacy_import_sample.csv"
    legacy.write_text(legacy_csv_text(), encoding="utf-8")

    paths = {"json": json_path, "csv": csv_path, "legacy": legacy}
    if reset_live:
        live = data / "inventory.json"
        Storage(live).save(products, movements)
        paths["live"] = live
    return paths


if __name__ == "__main__":
    for label, path in write_sample_files().items():
        print(f"wrote {label:<7} {path}")
