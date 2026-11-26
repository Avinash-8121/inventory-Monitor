# Inventory Stock Monitor

StudentLife Hack, Project #82 (Small Business and Retail). Tracks product quantities, flags low stock,
validates every entry, saves to local JSON/CSV, and reports on it. It comes with a menu-driven
terminal app and a modern Streamlit dashboard. Both use the same engine and the same data file.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt      # optional: the terminal app itself needs only the standard library

python app.py                        # terminal app (starts with 40 demo products)
streamlit run streamlit_app.py       # web dashboard (or: python app.py --gui)
```

Useful flags: `python app.py --reset-sample` restores the demo data, `--no-color` disables ANSI colours,
`--data PATH` uses a different data file.

Requires Python 3.10+.

## Feature checklist

| Brief requirement | Where it lives |
|---|---|
| **Interactive terminal interface** | `app.py`: branded welcome screen, grouped numbered menu, guided prompts, paginated tables |
| **Defensive input validation** | `core/validation.py`: loop-until-valid wrappers, `/cancel` at any prompt, all errors reported per record |
| **Core logic: track quantities, flag low stock** | `core/inventory.py` + `core/models.py`: sell, restock, stock count, edit, delete; status OK / LOW / OUT; movement ledger |
| **Local persistence (JSON/CSV)** | `core/storage.py`: atomic JSON writes, `.bak` backup, CSV mirror, corruption quarantine |
| **Search and query** | `core/search.py`: keyword (AND across SKU, name, category, supplier), category, status, price range, sort |
| **Executive summary** | `core/reports.py`: totals, averages, median, status counts, per-category table, top value, closest to running out, 30-day trend |
| **Multi-format export (stretch)** | Markdown report, plain-text audit log, CSV report, PNG dashboard chart (`exports/`) |
| **Threshold warning indicators (stretch)** | Startup banner and alerts screen: out of stock, critical (at or below 25% of reorder level), low, overdue restock (90+ days), stale stock, quantity and price anomalies |
| **GUI enhancement (stretch)** | `streamlit_app.py`: overview dashboard, filters, forms, charts, downloads, alerts, import |
| **Bulk dataset import (stretch)** | `core/importer.py`: header validation, column aliases, row-by-row checks, `exports/import_errors.log` |

## Demo script (matches section 9 of the brief)

1. `python app.py --reset-sample`. The welcome screen appears with a critical-alert banner.
2. **Option 9, Add product.** Type bad values on purpose: an existing SKU (`STN-001`), `??`, a blank name,
   quantity `lots`, `-4` and `2.5`, price `1.999`, reorder `-2`, and a future date. Each is caught and re-asked.
3. **Option 6, Record a sale** for the new item, then try selling more than is in stock. The sale is refused.
4. **Option 4, Executive summary**, then **option 3, Alerts**.
5. **Option 12, Bulk import.** Press Enter twice to use `data/legacy_import_sample.csv`. 6 rows import, 11 are
   rejected with line numbers and reasons, and the full log is written to `exports/import_errors.log`.
6. **Option 0, Exit.** Relaunch with `python app.py`: the new product, the sale and the imported rows are still there.
7. **Option 13, Export** for the Markdown report, text audit log, CSV and chart. Then `streamlit run streamlit_app.py`.

## Data model

`sku, name, category, quantity, unit_price, reorder_level, supplier, last_restocked, created_at`

A product is **OUT** at quantity 0, **LOW** at or below its reorder level, otherwise **OK**.
Every sale, restock, count and import is also written to a movement ledger, which feeds the trend metrics.

Validation limits: SKU 3-20 characters (letters, digits, hyphens); name up to 80; category up to 30;
quantity 0 to 1,000,000; price 0 to 1,000,000 with at most 2 decimals; dates `YYYY-MM-DD`, never in the future.

## Bulk import format

Required columns: `sku, name, category, quantity, unit_price, reorder_level`. Optional: `supplier, last_restocked`.
Aliases such as `Item Code`, `Qty`, `Price`, `Reorder Level` and `Vendor` are recognised. Comma, semicolon,
tab and pipe delimiters are detected, a UTF-8 BOM is handled, and `$` or thousands separators in numbers are cleaned.
Choose **skip** (keep existing products) or **update** (overwrite them) for SKUs that already exist.

## Project layout

```
app.py                  terminal app          streamlit_app.py   web dashboard
core/                   models, validation, storage, inventory, search, reports, importer, sample_data, console
data/                   inventory.json (live), sample_inventory.json/.csv, legacy_import_sample.csv
exports/                generated reports, charts and import logs
tests/                  45 tests (unit, scripted CLI sessions, Streamlit smoke test)
```

## Tests

```bash
python -m unittest discover -s tests -v      # or: pytest
```

Covers validation boundaries, stock logic, persistence across restarts, corrupted or missing files,
search, summaries, warnings, exports, import edge cases, scripted terminal sessions, and the dashboard pages.

## Troubleshooting

* **Damaged data file:** the app moves it aside as `inventory.json.corrupt-<timestamp>` and starts fresh.
  A `inventory.json.bak` copy of the previous save is kept on every write.
* **No colours on Windows:** use Windows Terminal or pass `--no-color`.
* **Charts skipped:** `pip install matplotlib`.
* **Dashboard will not start:** `pip install -r requirements.txt`.
