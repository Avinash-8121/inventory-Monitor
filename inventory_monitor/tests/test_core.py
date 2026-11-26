"""Unit tests for validation, inventory logic, storage, search, reports and import.

Run with:  python -m unittest discover -s tests -v     (or: pytest)
"""
import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import reports
from core.importer import import_csv_text
from core.inventory import Inventory, InventoryError
from core.models import StockStatus
from core.sample_data import LONG_NAME, build_sample, legacy_csv_text, write_sample_files
from core.search import search
from core.storage import Storage
from core.validation import (
    ValidationError, validate_date, validate_name, validate_price, validate_quantity,
    validate_record, validate_sku,
)

NOW = datetime(2026, 11, 24, 10, 0, 0)
GOOD = {"sku": "TST-001", "name": "Test Item", "category": "Testing", "quantity": 10,
        "unit_price": 2.5, "reorder_level": 5, "supplier": "Acme"}


def make_inventory(tmp: str) -> Inventory:
    return Inventory(Storage(Path(tmp) / "inv.json"), clock=lambda: NOW)


class ValidationTests(unittest.TestCase):
    def test_quantity_accepts_valid_and_rejects_bad(self):
        self.assertEqual(validate_quantity("12"), 12)
        self.assertEqual(validate_quantity("1,200"), 1200)
        self.assertEqual(validate_quantity(0), 0)
        for bad in ["", "  ", "abc", "-1", "5.5", "1e3", None, True, 10**9]:
            with self.assertRaises(ValidationError, msg=repr(bad)):
                validate_quantity(bad)

    def test_price(self):
        self.assertEqual(validate_price("4.5"), 4.5)
        self.assertEqual(validate_price(0), 0.0)
        for bad in ["", "abc", "-0.01", "2.999", "nan", "inf", "$5", 10**7]:
            with self.assertRaises(ValidationError, msg=repr(bad)):
                validate_price(bad)

    def test_sku_normalised_and_checked(self):
        self.assertEqual(validate_sku("  stn-001 "), "STN-001")
        for bad in ["", "ab", "??", "has space!", "-LEAD", "X" * 21]:
            with self.assertRaises(ValidationError, msg=repr(bad)):
                validate_sku(bad)

    def test_name_length_boundary(self):
        self.assertEqual(len(validate_name("x" * 80)), 80)
        with self.assertRaises(ValidationError):
            validate_name("x" * 81)
        with self.assertRaises(ValidationError):
            validate_name("bad\x00name")

    def test_dates(self):
        today = date(2026, 11, 24)
        self.assertEqual(validate_date("2026-11-24", today), "2026-11-24")
        self.assertEqual(validate_date("", today), "")
        for bad in ["2026-11-25", "24/11/2026", "2026-13-01", "1999-01-01"]:
            with self.assertRaises(ValidationError, msg=bad):
                validate_date(bad, today)

    def test_record_collects_all_errors(self):
        _, errors = validate_record({"sku": "??", "name": "", "quantity": "x"})
        fields = {e.split()[0] for e in errors}
        self.assertTrue({"sku", "name", "category", "quantity", "unit_price", "reorder_level"} <= fields)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.inv = make_inventory(self._tmp.name)

    def test_add_and_duplicate(self):
        p = self.inv.add(dict(GOOD))
        self.assertEqual(p.last_restocked, "2026-11-24")
        with self.assertRaises(InventoryError):
            self.inv.add(dict(GOOD))

    def test_status_boundaries(self):
        p = self.inv.add(dict(GOOD))            # qty 10, reorder 5
        self.assertIs(p.status, StockStatus.OK)
        self.inv.set_quantity("TST-001", 6)
        self.assertIs(p.status, StockStatus.OK)   # reorder+1
        self.inv.set_quantity("TST-001", 5)
        self.assertIs(p.status, StockStatus.LOW)  # exactly at reorder level
        self.inv.set_quantity("TST-001", 0)
        self.assertIs(p.status, StockStatus.OUT)

    def test_sell_restock_and_ledger(self):
        self.inv.add(dict(GOOD))
        self.inv.sell("tst-001", 4, "walk-in")
        self.inv.restock("TST-001", 20)
        self.assertEqual(self.inv.get("TST-001").quantity, 26)
        kinds = [m.kind for m in self.inv.history("TST-001")]
        self.assertCountEqual(kinds, ["initial", "sale", "restock"])
        self.assertEqual(self.inv.history("TST-001")[0].resulting_qty, 26)

    def test_cannot_oversell_or_use_bad_quantities(self):
        self.inv.add(dict(GOOD))
        with self.assertRaises(InventoryError):
            self.inv.sell("TST-001", 11)
        for bad in [0, -3, "x", 2.5]:
            with self.assertRaises(ValidationError):
                self.inv.sell("TST-001", bad)
        self.assertEqual(self.inv.get("TST-001").quantity, 10)

    def test_unknown_sku_suggests(self):
        self.inv.add(dict(GOOD))
        with self.assertRaises(InventoryError) as ctx:
            self.inv.get("TST-002")
        self.assertIn("TST-001", str(ctx.exception))

    def test_update_rules(self):
        self.inv.add(dict(GOOD))
        self.inv.update("TST-001", {"unit_price": "3.10", "name": "Renamed"})
        self.assertEqual(self.inv.get("TST-001").unit_price, 3.10)
        with self.assertRaises(InventoryError):
            self.inv.update("TST-001", {"quantity": 99})
        with self.assertRaises(ValidationError):
            self.inv.update("TST-001", {"unit_price": "-1"})

    def test_category_case_is_canonicalised(self):
        self.inv.add(dict(GOOD))
        p = self.inv.add({**GOOD, "sku": "TST-002", "category": "testing"})
        self.assertEqual(p.category, "Testing")

    def test_remove(self):
        self.inv.add(dict(GOOD))
        self.inv.remove("TST-001")
        self.assertNotIn("TST-001", self.inv.products)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_persistence_roundtrip(self):
        a = make_inventory(self._tmp.name)
        a.add(dict(GOOD))
        a.sell("TST-001", 3)
        b = make_inventory(self._tmp.name)                  # simulates a restart
        self.assertEqual(b.get("TST-001").quantity, 7)
        self.assertEqual(len(b.movements), 2)
        self.assertTrue((self.dir / "inv.csv").exists())    # CSV mirror

    def test_missing_file_creates_template(self):
        inv = make_inventory(self._tmp.name)
        self.assertEqual(inv.products, {})
        self.assertTrue(any("fresh inventory" in n for n in inv.notes))
        self.assertEqual(json.loads((self.dir / "inv.json").read_text())["products"], [])

    def test_corrupt_json_is_quarantined(self):
        (self.dir / "inv.json").write_text('{"products": [ {"sku": ', encoding="utf-8")
        inv = make_inventory(self._tmp.name)
        self.assertEqual(inv.products, {})
        self.assertTrue(list(self.dir.glob("inv.json.corrupt-*")))
        inv.add(dict(GOOD))                                  # still fully usable

    def test_wrong_shape_and_binary_garbage(self):
        (self.dir / "inv.json").write_text('{"hello": 1}', encoding="utf-8")
        self.assertEqual(make_inventory(self._tmp.name).products, {})
        (self.dir / "inv.json").write_bytes(b"\xff\xfe\x00garbage\x80")
        self.assertEqual(make_inventory(self._tmp.name).products, {})

    def test_bad_records_are_skipped_not_fatal(self):
        data = {"products": [
            {**GOOD, "quantity": "abc"}, {**GOOD, "sku": "OK-001"},
            {**GOOD, "sku": "OK-001"}, "not a dict"]}
        (self.dir / "inv.json").write_text(json.dumps(data), encoding="utf-8")
        inv = make_inventory(self._tmp.name)
        self.assertEqual(list(inv.products), ["OK-001"])
        self.assertEqual(len(inv.notes), 3)

    def test_backup_created_on_second_save(self):
        inv = make_inventory(self._tmp.name)
        inv.add(dict(GOOD))
        inv.sell("TST-001", 1)
        self.assertTrue((self.dir / "inv.json.bak").exists())


class SampleDataTests(unittest.TestCase):
    def test_forty_valid_products_with_edge_cases(self):
        products, movements = build_sample(date(2026, 11, 24))
        self.assertEqual(len(products), 40)
        self.assertEqual(len({p.sku for p in products}), 40)
        statuses = {p.status for p in products}
        self.assertEqual(statuses, {StockStatus.OK, StockStatus.LOW, StockStatus.OUT})
        self.assertTrue(any(p.quantity == p.reorder_level for p in products))      # boundary
        self.assertTrue(any(len(p.name) == 80 for p in products))                  # max-length name
        self.assertEqual(len(LONG_NAME), 80)
        self.assertTrue(any(p.unit_price == 0 for p in products))
        self.assertTrue(movements)

    def test_ledger_reconciles_with_quantities(self):
        products, movements = build_sample(date(2026, 11, 24))
        last = {}
        for m in sorted(movements, key=lambda m: m.timestamp):
            last[m.sku] = m.resulting_qty
        for p in products:
            self.assertEqual(last[p.sku], p.quantity, p.sku)

    def test_files_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_sample_files(tmp, today=date(2026, 11, 24))
            for p in paths.values():
                self.assertTrue(p.exists(), p)
            self.assertEqual(len(Inventory(Storage(paths["live"])).products), 40)


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.products, _ = build_sample(date(2026, 11, 24))

    def test_keyword_tokens_and(self):
        hits = search(self.products, keyword="usb cable")
        self.assertEqual([p.sku for p in hits], ["ELC-001"])

    def test_category_status_price_filters(self):
        hits = search(self.products, category="electronics", status="OUT")
        self.assertEqual([p.sku for p in hits], ["ELC-007"])
        cheap = search(self.products, max_price=1.0)
        self.assertTrue(cheap and all(p.unit_price <= 1.0 for p in cheap))
        self.assertEqual(search(self.products, keyword="zzzz-nothing"), [])

    def test_sorting(self):
        by_qty = search(self.products, sort_by="quantity", descending=True)
        self.assertEqual(by_qty[0].sku, "STN-008")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 24)
        self.products, self.movements = build_sample(self.today)

    def test_summary_totals(self):
        s = reports.summary(self.products, self.movements, self.today)
        self.assertEqual(s["total_products"], 40)
        self.assertEqual(s["total_units"], sum(p.quantity for p in self.products))
        self.assertAlmostEqual(s["total_value"], sum(p.stock_value for p in self.products), places=2)
        self.assertEqual(sum(s["status_counts"].values()), 40)
        self.assertEqual(sum(c["products"] for c in s["by_category"]), 40)
        self.assertGreater(s["trend"]["units_sold"], 0)

    def test_empty_summary_does_not_crash(self):
        s = reports.summary([], [], self.today)
        self.assertEqual(s["total_products"], 0)
        self.assertEqual(reports.warnings([], self.today), [])

    def test_warnings_cover_every_category(self):
        codes = {w.code for w in reports.warnings(self.products, self.today)}
        for expected in ["OUT_OF_STOCK", "CRITICAL_LOW", "LOW_STOCK", "OVERDUE_RESTOCK",
                         "STALE_RESTOCK", "ANOMALY_QUANTITY", "ANOMALY_PRICE"]:
            self.assertIn(expected, codes)

    def test_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = make_inventory(tmp)
            for p in self.products[:12]:
                inv.add({**p.to_dict(), "last_restocked": p.last_restocked})
            result = reports.export_reports(inv, Path(tmp) / "out", ("md", "txt", "csv"))
            names = sorted(p.suffix for p in result["files"])
            self.assertEqual(names, [".csv", ".md", ".txt"])
            md = next(p for p in result["files"] if p.suffix == ".md").read_text()
            self.assertIn("## Overview", md)
            csv_text = next(p for p in result["files"] if p.suffix == ".csv").read_text()
            self.assertTrue(csv_text.startswith("sku,name,category"))

    def test_chart_png(self):
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            self.skipTest("matplotlib not installed")
        s = reports.summary(self.products, self.movements, self.today)
        with tempfile.TemporaryDirectory() as tmp:
            path = reports.save_dashboard_chart(s, self.products, Path(tmp) / "c.png")
            self.assertGreater(path.stat().st_size, 10_000)


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.inv = make_inventory(self._tmp.name)
        self.inv.add({**GOOD, "sku": "STN-001"})

    def test_legacy_sample_row_by_row(self):
        log = Path(self._tmp.name) / "errors.log"
        r = import_csv_text(self.inv, legacy_csv_text(), source="legacy.csv", log_path=log)
        self.assertTrue(r.ok)
        self.assertEqual(r.imported, 6)
        self.assertEqual(r.skipped_existing, 1)
        self.assertEqual(r.failed, 11)
        self.assertEqual(r.blank_rows, 1)
        reasons = " | ".join(e.reason for e in r.errors)
        for needle in ["negative", "whole number", "name is required", "sku must be", "duplicate SKU",
                       "2 decimal", "expected 7 columns", "too large"]:
            self.assertIn(needle, reasons)
        self.assertIn("NEW-003", self.inv.products)                   # "1,200" and "$" handled
        self.assertEqual(self.inv.products["NEW-003"].quantity, 1200)
        self.assertEqual(self.inv.products["NEW-001"].unit_price, 6.5)
        self.assertEqual(self.inv.products["STN-001"].quantity, 10)    # skipped, untouched
        self.assertIn("LINE", log.read_text())

    def test_update_mode_overwrites(self):
        text = "sku,name,category,quantity,unit_price,reorder_level\nSTN-001,Renamed,Testing,99,9.99,3\n"
        r = import_csv_text(self.inv, text, mode="update")
        self.assertEqual((r.updated, r.failed), (1, 0))
        self.assertEqual(self.inv.get("STN-001").quantity, 99)
        self.assertEqual(self.inv.get("STN-001").name, "Renamed")

    def test_fatal_format_problems(self):
        for text, needle in [("", "empty"), ("sku,name\nA-1,x\n", "missing required column")]:
            r = import_csv_text(self.inv, text)
            self.assertFalse(r.ok)
            self.assertIn(needle, r.fatal)

    def test_semicolon_delimiter_and_aliases(self):
        text = "Code;Item;Category;Qty;Price;Reorder\nSEMI-01;Widget;Misc;5;1.50;2\n"
        r = import_csv_text(self.inv, text)
        self.assertEqual(r.imported, 1, r.errors)


if __name__ == "__main__":
    unittest.main()
