#!/usr/bin/env python3
"""Inventory Stock Monitor - menu-driven terminal application.

    python app.py                 start the terminal app
    python app.py --gui           launch the Streamlit dashboard instead
    python app.py --reset-sample  restore the 40-product demo dataset, then start
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from core import console as ui
from core import reports
from core.importer import import_csv_file
from core.inventory import Inventory, InventoryError
from core.models import Product
from core.sample_data import write_sample_files
from core.search import SORT_KEYS, search
from core.storage import Storage, StorageError
from core.validation import (
    Cancelled, ValidationError, prompt_choice, prompt_field, prompt_yes_no,
    validate_category, validate_date, validate_name, validate_positive_quantity,
    validate_price, validate_quantity, validate_reorder_level, validate_sku,
    validate_supplier,
)

BASE = Path(__file__).resolve().parent
DEFAULT_DATA = BASE / "data" / "inventory.json"
DEFAULT_EXPORTS = BASE / "exports"
LEGACY_SAMPLE = BASE / "data" / "legacy_import_sample.csv"

MENU = [
    ("VIEW", [("1", "View inventory"), ("2", "Search and filter"), ("3", "Low-stock alerts and warnings"),
              ("4", "Executive summary"), ("5", "Movement history")]),
    ("STOCK", [("6", "Record a sale"), ("7", "Restock a product"), ("8", "Adjust quantity (stock count)")]),
    ("MANAGE", [("9", "Add product"), ("10", "Edit product details"), ("11", "Delete product")]),
    ("DATA", [("12", "Bulk import from CSV"), ("13", "Export reports and chart"),
              ("14", "Launch the web dashboard (Streamlit)"), ("15", "Restore demo data")]),
]


def product_columns():
    status_style = lambda v: (ui.STATUS_COLOR[v], "bold")
    return [
        ("SKU", lambda p: p.sku, 9, "l"),
        ("Name", lambda p: p.name, 22, "l"),
        ("Category", lambda p: p.category, 12, "l"),
        ("Qty", lambda p: f"{p.quantity:,}", 6, "r"),
        ("Reorder", lambda p: f"{p.reorder_level:,}", 7, "r"),
        ("Price", lambda p: f"{p.unit_price:,.2f}", 9, "r"),
        ("Status", lambda p: p.status.value, 6, "l", status_style),
        ("Restocked", lambda p: p.last_restocked, 10, "l"),
    ]


class App:
    def __init__(self, inv: Inventory, export_dir: Path, data_dir: Path):
        self.inv = inv
        self.export_dir = export_dir
        self.data_dir = data_dir
        self.read = input
        self.actions = {
            "1": self.view, "2": self.find, "3": self.alerts, "4": self.show_summary,
            "5": self.history, "6": self.sell, "7": self.restock, "8": self.adjust,
            "9": self.add, "10": self.edit, "11": self.delete, "12": self.bulk_import,
            "13": self.export, "14": self.launch_gui, "15": self.restore,
        }

    # -------------------------------------------------------------- helpers
    def field(self, label, validator, **kw):
        return prompt_field(label, validator, reader=self.read, printer=ui.error, **kw)

    def pick(self) -> Product:
        """Choose a product by exact SKU or by keyword."""
        if not self.inv.products:
            raise InventoryError("The inventory is empty. Add a product first.")
        while True:
            raw = self.read("  SKU or name keyword: ").strip()
            if raw.lower() in {"/cancel", "/q"}:
                raise Cancelled()
            if not raw:
                ui.error("  [x] Enter a SKU or a word from the product name, or /cancel.")
                continue
            try:
                return self.inv.get(raw)
            except InventoryError:
                pass
            hits = search(self.inv.all(), keyword=raw)
            if len(hits) == 1:
                ui.info(f"Using {hits[0].sku} - {hits[0].name}")
                return hits[0]
            if hits:
                ui.info(f"{len(hits)} products match. Enter an exact SKU from this list:")
                print("\n".join(ui.table(hits[:10], product_columns())))
            else:
                ui.error(f"  [x] No product matches {raw!r}. Try another word, or /cancel.")

    def show_one(self, p: Product) -> None:
        print("\n".join(ui.table([p], product_columns())))

    def flag_if_needed(self, p: Product) -> None:
        for w in reports.warnings([p], self.inv.today):
            if w.level in ("CRITICAL", "WARNING") and not w.code.startswith("ANOMALY"):
                (ui.error if w.level == "CRITICAL" else ui.warn)(
                    f"  {'[!] ' if w.level == 'CRITICAL' else ''}{w.sku}: {w.message}")

    # ----------------------------------------------------------------- loop
    def print_menu(self) -> None:
        ui.heading("MAIN MENU")
        for group, items in MENU:
            print(ui.style(f"  {group}", "dim"))
            for key, label in items:
                print(f"   {ui.style(key.rjust(2), 'bold', 'cyan')}  {label}")
        print(f"   {ui.style(' 0', 'bold', 'cyan')}  Exit")

    def startup_alerts(self) -> None:
        for note in self.inv.notes:
            ui.warn(note)
        warns = reports.warnings(self.inv.all(), self.inv.today)
        crit = [w for w in warns if w.level == "CRITICAL"]
        other = len(warns) - len(crit)
        if not self.inv.products:
            ui.info("The inventory is empty. Add a product (option 9) or restore the demo data (option 15).")
            return
        if not warns:
            ui.ok(f"{len(self.inv.products)} products loaded. All stock levels are healthy.")
            return
        ui.info(f"{len(self.inv.products)} products loaded from {self.inv.storage.path.name}.")
        ui.error(f"  [!] {len(crit)} critical and {other} other alerts need attention (menu option 3).")
        for w in crit[:5]:
            print(ui.style(f"      {w.sku:<9} {w.message}", "red"))
        if len(crit) > 5:
            print(ui.style(f"      ...and {len(crit) - 5} more", "dim"))

    def run(self) -> None:
        ui.banner()
        self.startup_alerts()
        while True:
            self.print_menu()
            try:
                choice = self.read("\n  Choose an option: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if choice in {"0", "q", "quit", "exit"}:
                break
            action = self.actions.get(choice)
            if not action:
                ui.error(f"  [x] {choice!r} is not on the menu. Enter a number from 0 to {len(self.actions)}.")
                continue
            try:
                action()
            except Cancelled:
                ui.info("Cancelled. Nothing was changed.")
            except (ValidationError, InventoryError, StorageError) as exc:
                ui.error(f"  [x] {exc}")
            except (EOFError, KeyboardInterrupt):
                print()
                break
        print()
        ui.ok(f"Saved. Your data is in {self.inv.storage.path}. Goodbye.")

    # -------------------------------------------------------------- viewing
    def view(self) -> None:
        ui.heading("Inventory")
        sort_by = prompt_choice("Sort by", list(SORT_KEYS), default="name", reader=self.read, printer=ui.error)
        ui.show_table(search(self.inv.all(), sort_by=sort_by), product_columns(), reader=self.read)

    def find(self) -> None:
        ui.heading("Search and filter  (leave a filter blank to skip it)")
        cats = self.inv.categories()
        if cats:
            ui.info("Categories: " + ", ".join(cats))

        def category(v, t):
            if not v.strip():
                return ""
            for c in cats:
                if c.lower() == v.strip().lower():
                    return c
            raise ValidationError("is not one of the known categories")

        def status(v, t):
            if not v.strip():
                return ""
            if v.strip().upper() not in {"OK", "LOW", "OUT"}:
                raise ValidationError("must be OK, LOW or OUT")
            return v.strip().upper()

        kw = self.field("Keyword", lambda v, t: v.strip(), hint="name, SKU, supplier")
        cat = self.field("Category", category)
        st = self.field("Status", status, hint="OK/LOW/OUT")
        results = search(self.inv.all(), keyword=kw, category=cat, status=st)
        print()
        ui.show_table(results, product_columns(), reader=self.read)
        if results:
            ui.info(f"{len(results)} match(es); stock value {sum(p.stock_value for p in results):,.2f}.")

    def alerts(self) -> None:
        ui.heading("Low-stock alerts and warnings")
        warns = reports.warnings(self.inv.all(), self.inv.today)
        if not warns:
            ui.ok("No alerts. Every threshold is within limits.")
            return
        for w in warns:
            tag = ui.style(f"{w.level:<8}", ui.LEVEL_COLOR[w.level], "bold")
            print(f"  {tag} {w.sku:<9} {ui.truncate(w.name, 24):<24} {w.message}")
        counts = {lvl: sum(w.level == lvl for w in warns) for lvl in ("CRITICAL", "WARNING", "INFO")}
        ui.info(f"{counts['CRITICAL']} critical, {counts['WARNING']} warning, {counts['INFO']} info.")

    def show_summary(self) -> None:
        s = reports.summary(self.inv.all(), self.inv.movements, self.inv.today)
        sc = s["status_counts"]
        ui.heading(f"Executive summary  (as of {s['as_of']})")
        if not s["total_products"]:
            ui.info("No products yet. Add one or restore the demo data.")
            return
        n_prod, n_units, n_value = f"{s['total_products']:,}", f"{s['total_units']:,}", f"{s['total_value']:,.2f}"
        print(f"  Products {ui.style(n_prod, 'bold')}   Units on hand {ui.style(n_units, 'bold')}   "
              f"Stock value {ui.style(n_value, 'bold')}")
        print(f"  Average price {s['avg_price']:,.2f}   Average quantity {s['avg_quantity']:,}   "
              f"Median quantity {s['median_quantity']:,}")
        print()
        for name, color in (("OK", "green"), ("LOW", "yellow"), ("OUT", "red")):
            print(f"  {ui.style(name.ljust(4), color, 'bold')} {ui.style(ui.bar(sc[name], s['total_products']), color)} {sc[name]}")
        print(f"  Healthy stock: {s['health_pct']}% of products")

        ui.heading("By category")
        cols = [("Category", lambda r: r["category"], 20, "l"), ("Products", lambda r: r["products"], 8, "r"),
                ("Units", lambda r: f"{r['units']:,}", 8, "r"), ("Value", lambda r: f"{r['value']:,.2f}", 12, "r"),
                ("Attention", lambda r: r["attention"], 9, "r", lambda v: ("yellow",) if int(v) else ())]
        print("\n".join(ui.table(s["by_category"], cols)))

        ui.heading("Highest stock value")
        for sku, name, value in s["top_value"]:
            print(f"  {sku:<9} {ui.truncate(name, 34):<34} {value:>12,.2f}")
        ui.heading("Closest to running out")
        for sku, name, qty, reorder in s["lowest_stock"]:
            print(f"  {sku:<9} {ui.truncate(name, 34):<34} {qty:>6} on hand / reorder at {reorder}")

        t = s["trend"]
        ui.heading(f"Trend  (last {t['window_days']} days)")
        print("  " + reports._trend_sentence(t))
        if t["top_sellers"]:
            print("  Top sellers: " + ", ".join(f"{k} ({v})" for k, v in t["top_sellers"]))

    def history(self) -> None:
        ui.heading("Movement history  (latest 20)")
        sku = self.field("SKU (blank for all products)", lambda v, t: validate_sku(v) if v.strip() else "")
        items = self.inv.history(sku or None, limit=20)
        cols = [("When", lambda m: m.timestamp.replace("T", " "), 19, "l"), ("SKU", lambda m: m.sku, 9, "l"),
                ("Type", lambda m: m.kind, 10, "l"), ("Change", lambda m: f"{m.delta:+,}", 8, "r"),
                ("Now", lambda m: f"{m.resulting_qty:,}", 7, "r"), ("Note", lambda m: m.note, 28, "l")]
        ui.show_table(items, cols, reader=self.read)

    # ---------------------------------------------------------- stock changes
    def sell(self) -> None:
        ui.heading("Record a sale")
        p = self.pick()
        self.show_one(p)
        qty = self.field("Units sold", validate_positive_quantity)
        note = self.field("Note", validate_supplier, default="")
        p = self.inv.sell(p.sku, qty, note)
        ui.ok(f"Sold {qty} x {p.name}. {p.quantity} left ({p.status.value}).")
        self.flag_if_needed(p)

    def restock(self) -> None:
        ui.heading("Restock a product")
        p = self.pick()
        self.show_one(p)
        qty = self.field("Units received", validate_positive_quantity)
        note = self.field("Note", validate_supplier, default="")
        p = self.inv.restock(p.sku, qty, note)
        ui.ok(f"Restocked {qty} x {p.name}. Now {p.quantity} ({p.status.value}).")

    def adjust(self) -> None:
        ui.heading("Adjust quantity (stock count)")
        p = self.pick()
        self.show_one(p)
        new_qty = self.field("Counted quantity", validate_quantity)
        note = self.field("Reason", validate_supplier, default="Stock count")
        p = self.inv.set_quantity(p.sku, new_qty, note)
        ui.ok(f"{p.sku} set to {p.quantity} ({p.status.value}).")
        self.flag_if_needed(p)

    # ------------------------------------------------------------ management
    def add(self) -> None:
        ui.heading("Add product  (type /cancel at any prompt to stop)")

        def new_sku(v, t):
            sku = validate_sku(v, t)
            if sku in self.inv.products:
                raise ValidationError(f"already exists ({self.inv.products[sku].name}); choose another SKU")
            return sku

        cats = self.inv.categories()
        if cats:
            ui.info("Existing categories: " + ", ".join(cats))
        raw = {
            "sku": self.field("SKU", new_sku, hint="e.g. STN-009"),
            "name": self.field("Name", validate_name),
            "category": self.field("Category", validate_category),
            "quantity": self.field("Quantity on hand", validate_quantity),
            "unit_price": self.field("Unit price", validate_price, hint="e.g. 4.99"),
            "reorder_level": self.field("Reorder level", validate_reorder_level, hint="flag as LOW at or below this"),
            "supplier": self.field("Supplier", validate_supplier, default="", hint="optional"),
            "last_restocked": self.field("Last restocked", validate_date, default="", hint="YYYY-MM-DD, blank = today"),
        }
        p = self.inv.add(raw)
        ui.ok(f"Added {p.sku}.")
        self.show_one(p)
        self.flag_if_needed(p)

    def edit(self) -> None:
        ui.heading("Edit product  (Enter keeps the current value; '-' clears the supplier)")
        p = self.pick()
        self.show_one(p)
        supplier = self.field("Supplier", lambda v, t: "" if v.strip() == "-" else validate_supplier(v, t),
                              default=p.supplier)
        changes = {
            "name": self.field("Name", validate_name, default=p.name),
            "category": self.field("Category", validate_category, default=p.category),
            "unit_price": self.field("Unit price", validate_price, default=f"{p.unit_price:.2f}"),
            "reorder_level": self.field("Reorder level", validate_reorder_level, default=p.reorder_level),
            "supplier": supplier,
        }
        p = self.inv.update(p.sku, changes)
        ui.ok(f"Updated {p.sku}.")
        self.show_one(p)
        self.flag_if_needed(p)

    def delete(self) -> None:
        ui.heading("Delete product")
        p = self.pick()
        self.show_one(p)
        typed = self.read(f"  Type {p.sku} to confirm deletion (anything else cancels): ").strip().upper()
        if typed != p.sku:
            ui.info("Cancelled. Nothing was deleted.")
            return
        self.inv.remove(p.sku)
        ui.ok(f"Deleted {p.sku}.")

    # ----------------------------------------------------------------- data
    def bulk_import(self) -> None:
        ui.heading("Bulk import from CSV")
        ui.info("Required columns: sku, name, category, quantity, unit_price, reorder_level.")
        ui.info("Optional: supplier, last_restocked. Common aliases (qty, price, vendor...) are understood.")
        default = str(LEGACY_SAMPLE) if LEGACY_SAMPLE.exists() else None
        def csv_path(v, t):
            text = v.strip().strip('"')
            if not text:
                raise ValidationError("is required")
            return text

        path = self.field("CSV file path", csv_path, default=default)
        mode = prompt_choice("If a SKU already exists", ["skip", "update"], default="skip",
                             reader=self.read, printer=ui.error)
        log = self.export_dir / "import_errors.log"
        result = import_csv_file(self.inv, path, mode=mode, log_path=log)
        print()
        if not result.ok:
            ui.error(f"  [x] {result.headline()}")
            return
        ui.ok(result.headline())
        if result.errors:
            cols = [("Line", lambda e: e.row, 5, "r"), ("SKU", lambda e: e.sku, 10, "l"),
                    ("Reason", lambda e: e.reason, 62, "l")]
            print("\n".join(ui.table(result.errors[:10], cols)))
            if len(result.errors) > 10:
                ui.info(f"...and {len(result.errors) - 10} more.")
            ui.warn(f"Full row-by-row error log: {result.log_path}")
        else:
            ui.ok("Every row imported cleanly.")

    def export(self) -> None:
        ui.heading("Export reports and chart")
        fmt = prompt_choice("Format", ["all", "md", "txt", "csv", "png"], default="all",
                            reader=self.read, printer=ui.error)
        formats = ("md", "txt", "csv", "png") if fmt == "all" else (fmt,)
        result = reports.export_reports(self.inv, self.export_dir, formats)
        for path in result["files"]:
            ui.ok(f"Wrote {path}")
        for reason in result["skipped"]:
            ui.warn(reason)

    def launch_gui(self) -> None:
        if importlib.util.find_spec("streamlit") is None:
            ui.warn("Streamlit is not installed. Run:  pip install -r requirements.txt")
            return
        ui.info("Starting the dashboard. Press Ctrl+C in this window to return.")
        env = dict(os.environ, INVENTORY_DATA=str(self.inv.storage.path), INVENTORY_EXPORTS=str(self.export_dir))
        try:
            subprocess.run([sys.executable, "-m", "streamlit", "run", str(BASE / "streamlit_app.py")],
                           env=env, check=False)
        except KeyboardInterrupt:
            pass
        self.inv = Inventory(Storage(self.inv.storage.path))   # pick up changes made in the GUI

    def restore(self) -> None:
        ui.heading("Restore demo data")
        ui.warn("This replaces your current inventory with the 40-product demo dataset.")
        if not prompt_yes_no("Continue?", default=False, reader=self.read, printer=ui.error):
            ui.info("Cancelled. Nothing was changed.")
            return
        write_sample_files(self.data_dir, reset_live=False)
        self.inv.storage.save(*_sample())
        self.inv = Inventory(self.inv.storage)
        ui.ok(f"Loaded {len(self.inv.products)} demo products.")


def _sample():
    from core.sample_data import build_sample
    return build_sample()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inventory Stock Monitor")
    parser.add_argument("--data", default=os.environ.get("INVENTORY_DATA", str(DEFAULT_DATA)),
                        help="path to the JSON data file (default: data/inventory.json)")
    parser.add_argument("--export-dir", default=str(DEFAULT_EXPORTS), help="where reports are written")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colours")
    parser.add_argument("--reset-sample", action="store_true", help="restore the demo dataset before starting")
    parser.add_argument("--gui", action="store_true", help="launch the Streamlit dashboard")
    args = parser.parse_args(argv)

    ui.init(False if args.no_color else None)
    data_path = Path(args.data)

    if args.reset_sample:
        products, movements = _sample()
        Storage(data_path).save(products, movements)
        write_sample_files(data_path.parent, reset_live=False)

    if args.gui:
        if importlib.util.find_spec("streamlit") is None:
            print("Streamlit is not installed. Run: pip install -r requirements.txt")
            return 1
        env = dict(os.environ, INVENTORY_DATA=str(data_path), INVENTORY_EXPORTS=args.export_dir)
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(BASE / "streamlit_app.py")], env=env)

    try:
        inv = Inventory(Storage(data_path))
    except StorageError as exc:
        print(f"Cannot open the data file: {exc}")
        return 1
    App(inv, Path(args.export_dir), data_path.parent).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
