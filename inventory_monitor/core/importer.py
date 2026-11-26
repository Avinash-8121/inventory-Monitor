"""Bulk CSV import: header validation, row-by-row checks and an error log."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .inventory import Inventory, InventoryError
from .validation import ValidationError, validate_record

REQUIRED = ["sku", "name", "category", "quantity", "unit_price", "reorder_level"]
OPTIONAL = ["supplier", "last_restocked"]
ALIASES = {
    "item": "name", "product": "name", "product_name": "name", "description": "name",
    "qty": "quantity", "stock": "quantity", "on_hand": "quantity",
    "price": "unit_price", "unitprice": "unit_price", "cost": "unit_price",
    "reorder": "reorder_level", "reorder_point": "reorder_level", "min_stock": "reorder_level",
    "min_qty": "reorder_level", "vendor": "supplier", "last_restock": "last_restocked",
    "restocked": "last_restocked", "code": "sku", "item_code": "sku",
}


@dataclass
class RowError:
    row: int          # line number in the file (header is line 1)
    sku: str
    reason: str


@dataclass
class ImportResult:
    source: str
    total_rows: int = 0
    imported: int = 0
    updated: int = 0
    skipped_existing: int = 0
    blank_rows: int = 0
    errors: list[RowError] = field(default_factory=list)
    fatal: str = ""
    log_path: Path | None = None

    @property
    def failed(self) -> int:
        return len(self.errors)

    @property
    def ok(self) -> bool:
        return not self.fatal

    def headline(self) -> str:
        if self.fatal:
            return f"Import failed: {self.fatal}"
        return (f"{self.total_rows} data rows read: {self.imported} imported, {self.updated} updated, "
                f"{self.skipped_existing} skipped (SKU exists), {self.failed} rejected, "
                f"{self.blank_rows} blank.")


def _norm_header(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return ALIASES.get(key, key)


def _clean_money(text: str) -> str:
    cleaned = re.sub(r"[$\u20b9\u20ac\u00a3\s]", "", text or "")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", cleaned):
        cleaned = cleaned.replace(",", "")
    return cleaned


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def import_csv_text(inv: Inventory, text: str, *, source: str = "uploaded.csv",
                    mode: str = "skip", log_path: str | Path | None = None) -> ImportResult:
    """Import CSV text. mode='skip' keeps existing SKUs untouched; 'update' overwrites them."""
    result = ImportResult(source=source)
    if mode not in {"skip", "update"}:
        result.fatal = f"unknown mode {mode!r} (use 'skip' or 'update')"
        return _finish(result, log_path)
    if not text.strip():
        result.fatal = "the file is empty"
        return _finish(result, log_path)

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect, skipinitialspace=True)

    try:
        header_raw = next(reader)
    except StopIteration:
        result.fatal = "the file has no header row"
        return _finish(result, log_path)
    header = [_norm_header(h) for h in header_raw]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        result.fatal = (f"missing required column(s): {', '.join(missing)}. "
                        f"Found: {', '.join(h or '(blank)' for h in header_raw)}")
        return _finish(result, log_path)
    dupes = {h for h in header if h and header.count(h) > 1}
    if dupes:
        result.fatal = f"duplicate column(s) after normalising names: {', '.join(sorted(dupes))}"
        return _finish(result, log_path)

    first_seen: dict[str, int] = {}
    with inv.batch():
        for fields in reader:
            line = reader.line_num
            if not any(f.strip() for f in fields):
                result.blank_rows += 1
                continue
            result.total_rows += 1
            row = dict(zip(header, fields))
            sku_hint = (row.get("sku") or "").strip().upper() or "?"

            if len(fields) != len(header):
                result.errors.append(RowError(
                    line, sku_hint, f"expected {len(header)} columns but found {len(fields)}"))
                continue
            row["unit_price"] = _clean_money(row.get("unit_price", ""))
            clean, errors = validate_record({k: row.get(k, "") for k in REQUIRED + OPTIONAL}, inv.today)
            if errors:
                result.errors.append(RowError(line, sku_hint, "; ".join(errors)))
                continue
            sku = clean["sku"]
            if sku in first_seen:
                result.errors.append(RowError(line, sku, f"duplicate SKU in file (first seen on line {first_seen[sku]})"))
                continue
            first_seen[sku] = line

            try:
                if sku in inv.products:
                    if mode == "skip":
                        result.skipped_existing += 1
                        continue
                    inv.update(sku, {k: clean[k] for k in ("name", "category", "unit_price", "reorder_level", "supplier")})
                    if inv.products[sku].quantity != clean["quantity"]:
                        inv.set_quantity(sku, clean["quantity"], f"Bulk import update ({source})")
                    result.updated += 1
                else:
                    inv.add(clean, kind="import", note=f"Bulk import ({source})")
                    result.imported += 1
            except (ValidationError, InventoryError) as exc:
                result.errors.append(RowError(line, sku, str(exc)))
    return _finish(result, log_path)


def import_csv_file(inv: Inventory, path: str | Path, *, mode: str = "skip",
                    log_path: str | Path | None = "exports/import_errors.log") -> ImportResult:
    p = Path(path).expanduser()
    try:
        text = read_text(p)
    except FileNotFoundError:
        return _finish(ImportResult(source=p.name, fatal=f"file not found: {p}"), log_path)
    except OSError as exc:
        return _finish(ImportResult(source=p.name, fatal=f"cannot read file: {exc}"), log_path)
    return import_csv_text(inv, text, source=p.name, mode=mode, log_path=log_path)


def render_log(result: ImportResult) -> str:
    L = [f"Import log - {datetime.now().isoformat(timespec='seconds')}",
         f"Source: {result.source}", result.headline(), ""]
    if result.errors:
        L.append(f"{'LINE':>5}  {'SKU':<12} REASON")
        L += [f"{e.row:>5}  {e.sku:<12} {e.reason}" for e in result.errors]
    else:
        L.append("No row errors.")
    return "\n".join(L) + "\n"


def _finish(result: ImportResult, log_path) -> ImportResult:
    if log_path:
        try:
            path = Path(log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_log(result), encoding="utf-8")
            result.log_path = path
        except OSError:
            pass
    return result
