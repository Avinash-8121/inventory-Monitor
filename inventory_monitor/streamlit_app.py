"""Inventory Stock Monitor - Streamlit dashboard.

    streamlit run streamlit_app.py

Uses the same core engine as the terminal app, so both share data/inventory.json.
"""
from __future__ import annotations

import io
import os
import sys
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import reports  # noqa: E402
from core.importer import import_csv_text, read_text, render_log  # noqa: E402
from core.inventory import Inventory, InventoryError  # noqa: E402
from core.sample_data import build_sample, write_sample_files  # noqa: E402
from core.search import SORT_KEYS, search  # noqa: E402
from core.storage import Storage, StorageError  # noqa: E402
from core.validation import MAX_PRICE, MAX_QTY, MAX_REORDER, ValidationError  # noqa: E402

DATA_PATH = Path(os.environ.get("INVENTORY_DATA", ROOT / "data" / "inventory.json"))
EXPORT_DIR = Path(os.environ.get("INVENTORY_EXPORTS", ROOT / "exports"))
LEGACY_SAMPLE = ROOT / "data" / "legacy_import_sample.csv"

STATUS_COLORS = {"OK": "#2F6F4E", "LOW": "#C77D0A", "OUT": "#B42318"}
LEVEL_COLORS = {"CRITICAL": "#B42318", "WARNING": "#C77D0A", "INFO": "#0F766E"}
PAGES = ["Overview", "Inventory", "Stock in and out", "Products", "Reports", "Bulk import", "Alerts"]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, button, input, textarea { font-family: 'Plus Jakarta Sans', system-ui, sans-serif; }
h1 { font-weight: 700; letter-spacing: -0.02em; margin-bottom: 0.1rem; }
h2, h3 { font-weight: 600; letter-spacing: -0.01em; }
[data-testid="stMetric"] { background: #FFFFFF; border: 1px solid #D5DEDE; border-radius: 10px; padding: 14px 16px; }
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; font-weight: 700; }
[data-testid="stSidebar"] { border-right: 1px solid #D5DEDE; }
.chip { display: inline-block; padding: 2px 11px; margin-right: 6px; border-radius: 999px; font-size: 0.82rem; font-weight: 600; color: #fff; }
.chip.crit { background: #B42318; } .chip.warn { background: #C77D0A; } .chip.good { background: #2F6F4E; }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
</style>
"""


# ------------------------------------------------------------------ helpers
def rerun() -> None:
    (st.rerun if hasattr(st, "rerun") else st.experimental_rerun)()


def flash(kind: str, message: str) -> None:
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    item = st.session_state.pop("_flash", None)
    if item:
        getattr(st, item[0])(item[1])


def show_errors(exc: Exception) -> None:
    parts = [p.strip() for p in str(exc).split("; ") if p.strip()]
    st.error("\n".join(f"- {p}" for p in parts) if len(parts) > 1 else parts[0])


def label_for(inv: Inventory):
    return lambda sku: f"{sku} - {inv.products[sku].name} ({inv.products[sku].quantity:,} in stock)"


def style_column(df: pd.DataFrame, column: str, colors: dict):
    styler = df.style
    apply = getattr(styler, "map", None) or styler.applymap
    return apply(lambda v: f"color: {colors.get(v, 'inherit')}; font-weight: 600", subset=[column])


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


# ------------------------------------------------------------------- pages
def page_overview(inv: Inventory) -> None:
    if not inv.products:
        st.info("No products yet. Add one on the Products page, import a CSV, or restore the demo data from the sidebar.")
        return
    s = reports.summary(inv.all(), inv.movements, inv.today)
    sc = s["status_counts"]
    cols = st.columns(5)
    cols[0].metric("Products", f"{s['total_products']:,}")
    cols[1].metric("Units on hand", f"{s['total_units']:,}")
    cols[2].metric("Stock value", f"{s['total_value']:,.2f}")
    cols[3].metric("Need attention", f"{sc['LOW'] + sc['OUT']}", f"{sc['OUT']} out of stock", delta_color="inverse")
    cols[4].metric("Healthy stock", f"{s['health_pct']}%")

    warns = reports.warnings(inv.all(), inv.today)
    crit = [w for w in warns if w.level == "CRITICAL"]
    if crit:
        st.subheader("Reorder now")
        for w in crit[:6]:
            st.error(f"**{w.sku}** {w.name}: {w.message}")
        if len(crit) > 6:
            st.caption(f"{len(crit) - 6} more critical alerts on the Alerts page.")

    left, right = st.columns(2)
    with left:
        st.subheader("Stock status")
        df = pd.DataFrame({"Status": list(sc), "Products": list(sc.values())})
        st.altair_chart(
            alt.Chart(df).mark_arc(innerRadius=60).encode(
                theta="Products:Q",
                color=alt.Color("Status:N", scale=alt.Scale(domain=list(STATUS_COLORS), range=list(STATUS_COLORS.values()))),
                tooltip=["Status", "Products"]).properties(height=280),
            use_container_width=True)
    with right:
        st.subheader("Stock value by category")
        cat = pd.DataFrame(s["by_category"]).rename(columns={"category": "Category", "value": "Value", "products": "Products", "units": "Units", "attention": "Attention"})
        st.altair_chart(
            alt.Chart(cat).mark_bar(color="#0F766E").encode(
                x=alt.X("Value:Q", title="Stock value"), y=alt.Y("Category:N", sort="-x", title=None),
                tooltip=["Category", "Products", "Units", "Value"]).properties(height=280),
            use_container_width=True)

    t = s["trend"]
    st.subheader(f"Movement, last {t['window_days']} days")
    st.caption(reports._trend_sentence(t))
    daily = pd.DataFrame(t["daily"], columns=["Date", "Sold", "Restocked"]).melt("Date", var_name="Type", value_name="Units")
    st.altair_chart(
        alt.Chart(daily).mark_line(point=True).encode(
            x=alt.X("Date:T"), y="Units:Q",
            color=alt.Color("Type:N", scale=alt.Scale(domain=["Sold", "Restocked"], range=["#B42318", "#2F6F4E"])),
            tooltip=["Date:T", "Type", "Units"]).properties(height=260),
        use_container_width=True)

    a, b = st.columns(2)
    with a:
        st.subheader("Highest stock value")
        st.dataframe(pd.DataFrame(s["top_value"], columns=["SKU", "Product", "Value"]).style.format({"Value": "{:,.2f}"}),
                     hide_index=True, use_container_width=True)
    with b:
        st.subheader("Closest to running out")
        st.dataframe(pd.DataFrame(s["lowest_stock"], columns=["SKU", "Product", "On hand", "Reorder level"]),
                     hide_index=True, use_container_width=True)


def page_inventory(inv: Inventory) -> None:
    if not inv.products:
        st.info("The inventory is empty.")
        return
    c1, c2, c3 = st.columns([2, 1, 1])
    keyword = c1.text_input("Search", placeholder="Name, SKU, category or supplier")
    category = c2.selectbox("Category", ["All categories"] + inv.categories())
    statuses = c3.multiselect("Status", ["OK", "LOW", "OUT"], placeholder="Any status")
    d1, d2, d3, d4 = st.columns(4)
    min_price = d1.number_input("Minimum price", min_value=0.0, value=0.0, step=1.0, format="%.2f")
    max_price = d2.number_input("Maximum price", min_value=0.0, value=float(MAX_PRICE), step=1.0, format="%.2f")
    sort_by = d3.selectbox("Sort by", list(SORT_KEYS))
    descending = d4.checkbox("Descending")

    results = search(inv.all(), keyword=keyword, category="" if category.startswith("All") else category,
                     status=statuses, min_price=min_price or None,
                     max_price=max_price if max_price < MAX_PRICE else None,
                     sort_by=sort_by, descending=descending)
    st.caption(f"{len(results)} of {len(inv.products)} products. Stock value of this selection: "
               f"{sum(p.stock_value for p in results):,.2f}")
    if not results:
        st.info("No products match these filters. Clear a filter to see more.")
        return
    df = reports.to_dataframe(results, inv.today)
    st.dataframe(style_column(df, "Status", STATUS_COLORS).format({"Unit price": "{:,.2f}", "Stock value": "{:,.2f}"}),
                 hide_index=True, use_container_width=True, height=min(640, 38 + 35 * len(df)))
    st.download_button("Download this selection (CSV)", reports.to_csv_report(results, inv.today),
                       "inventory_selection.csv", "text/csv")


def _pick_product(inv: Inventory, key: str):
    skus = sorted(inv.products)
    return st.selectbox("Product", skus, format_func=label_for(inv), key=key)


def _after_stock_change(inv: Inventory, sku: str, verb: str) -> None:
    p = inv.products[sku]
    flash("success", f"{verb} {p.sku}. Now {p.quantity:,} in stock ({p.status.value}).")
    rerun()


def page_stock(inv: Inventory) -> None:
    if not inv.products:
        st.info("Add a product first.")
        return
    sale_tab, restock_tab, count_tab = st.tabs(["Record a sale", "Restock", "Stock count"])
    with sale_tab:
        with st.form("sale_form", clear_on_submit=True):
            sku = _pick_product(inv, "sale_sku")
            qty = st.number_input("Units sold", min_value=1, max_value=MAX_QTY, value=1, step=1)
            note = st.text_input("Note (optional)", key="sale_note")
            if st.form_submit_button("Record sale", type="primary"):
                try:
                    inv.sell(sku, qty, note)
                    _after_stock_change(inv, sku, "Recorded sale for")
                except (ValidationError, InventoryError, StorageError) as exc:
                    show_errors(exc)
    with restock_tab:
        with st.form("restock_form", clear_on_submit=True):
            sku = _pick_product(inv, "restock_sku")
            qty = st.number_input("Units received", min_value=1, max_value=MAX_QTY, value=10, step=1)
            note = st.text_input("Note (optional)", key="restock_note")
            if st.form_submit_button("Record restock", type="primary"):
                try:
                    inv.restock(sku, qty, note)
                    _after_stock_change(inv, sku, "Restocked")
                except (ValidationError, InventoryError, StorageError) as exc:
                    show_errors(exc)
    with count_tab:
        with st.form("count_form", clear_on_submit=True):
            sku = _pick_product(inv, "count_sku")
            qty = st.number_input("Counted quantity", min_value=0, max_value=MAX_QTY, value=0, step=1)
            note = st.text_input("Reason", value="Stock count", key="count_note")
            if st.form_submit_button("Save count", type="primary"):
                try:
                    inv.set_quantity(sku, qty, note)
                    _after_stock_change(inv, sku, "Updated count for")
                except (ValidationError, InventoryError, StorageError) as exc:
                    show_errors(exc)

    st.subheader("Recent movements")
    rows = [{"When": m.timestamp.replace("T", " "), "SKU": m.sku, "Type": m.kind, "Change": m.delta,
             "Now": m.resulting_qty, "Note": m.note} for m in inv.history(limit=15)]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def page_products(inv: Inventory) -> None:
    new_tab, edit_tab, delete_tab = st.tabs(["Add product", "Edit product", "Delete product"])
    with new_tab:
        if inv.categories():
            st.caption("Existing categories: " + ", ".join(inv.categories()))
        with st.form("add_form", clear_on_submit=True):
            a, b = st.columns(2)
            sku = a.text_input("SKU", placeholder="STN-009", key="add_sku")
            name = b.text_input("Name", key="add_name")
            c, d = st.columns(2)
            category = c.text_input("Category", key="add_category")
            supplier = d.text_input("Supplier (optional)", key="add_supplier")
            e, f, g, h = st.columns(4)
            qty = e.number_input("Quantity on hand", min_value=0, max_value=MAX_QTY, value=0, step=1, key="add_qty")
            price = f.number_input("Unit price", min_value=0.0, max_value=float(MAX_PRICE), value=0.0, step=0.01, format="%.2f", key="add_price")
            reorder = g.number_input("Reorder level", min_value=0, max_value=MAX_REORDER, value=10, step=1, key="add_reorder")
            restocked = h.date_input("Last restocked", value=date.today(), max_value=date.today(), key="add_restocked")
            if st.form_submit_button("Add product", type="primary"):
                try:
                    p = inv.add({"sku": sku, "name": name, "category": category, "quantity": qty,
                                 "unit_price": price, "reorder_level": reorder, "supplier": supplier,
                                 "last_restocked": restocked.isoformat()})
                    flash("success", f"Added {p.sku} - {p.name}.")
                    rerun()
                except (ValidationError, InventoryError, StorageError) as exc:
                    show_errors(exc)
    with edit_tab:
        if not inv.products:
            st.info("Nothing to edit yet.")
        else:
            sku = _pick_product(inv, "edit_sku")
            p = inv.products[sku]
            st.caption("Quantity changes go through Stock in and out so every change is recorded.")
            with st.form(f"edit_form_{sku}"):
                a, b = st.columns(2)
                name = a.text_input("Name", value=p.name, key=f"edit_name_{sku}")
                category = b.text_input("Category", value=p.category, key=f"edit_category_{sku}")
                c, d, e = st.columns(3)
                price = c.number_input("Unit price", min_value=0.0, max_value=float(MAX_PRICE), value=float(p.unit_price), step=0.01, format="%.2f", key=f"edit_price_{sku}")
                reorder = d.number_input("Reorder level", min_value=0, max_value=MAX_REORDER, value=int(p.reorder_level), step=1, key=f"edit_reorder_{sku}")
                supplier = e.text_input("Supplier", value=p.supplier, key=f"edit_supplier_{sku}")
                if st.form_submit_button("Save changes", type="primary"):
                    try:
                        inv.update(sku, {"name": name, "category": category, "unit_price": price,
                                         "reorder_level": reorder, "supplier": supplier})
                        flash("success", f"Saved changes to {sku}.")
                        rerun()
                    except (ValidationError, InventoryError, StorageError) as exc:
                        show_errors(exc)
    with delete_tab:
        if not inv.products:
            st.info("Nothing to delete.")
        else:
            sku = _pick_product(inv, "delete_sku")
            confirm = st.checkbox(f"I understand that deleting {sku} cannot be undone.", key=f"confirm_{sku}")
            if st.button("Delete product", disabled=not confirm, type="primary"):
                try:
                    inv.remove(sku)
                    flash("success", f"Deleted {sku}.")
                    rerun()
                except (InventoryError, StorageError) as exc:
                    show_errors(exc)


def page_reports(inv: Inventory) -> None:
    if not inv.products:
        st.info("There is nothing to report yet.")
        return
    today = inv.today
    prods = sorted(inv.all(), key=lambda p: p.sku)
    s = reports.summary(prods, inv.movements, today)
    warns = reports.warnings(prods, today)

    md = reports.to_markdown(s, warns, prods, today)
    txt = reports.to_text_audit(s, warns, prods, inv.movements, today)
    csv_text = reports.to_csv_report(prods, today)
    a, b, c, d = st.columns(4)
    a.download_button("Markdown report", md, "stock_report.md", "text/markdown", use_container_width=True)
    b.download_button("Text audit log", txt, "audit_log.txt", "text/plain", use_container_width=True)
    c.download_button("CSV report", csv_text, "stock_report.csv", "text/csv", use_container_width=True)

    fig = reports.build_dashboard_figure(s, prods)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    d.download_button("Dashboard chart (PNG)", buf.getvalue(), "dashboard.png", "image/png", use_container_width=True)
    st.pyplot(fig)
    import matplotlib.pyplot as plt
    plt.close(fig)

    if st.button("Save all formats to the exports folder"):
        result = reports.export_reports(inv, EXPORT_DIR)
        st.success("Saved: " + ", ".join(p.name for p in result["files"]) + f"  (in {EXPORT_DIR})")
        for reason in result["skipped"]:
            st.warning(reason)
    with st.expander("Preview the Markdown report"):
        st.markdown(md)


def page_import(inv: Inventory) -> None:
    st.write("Upload a legacy CSV. Required columns: `sku`, `name`, `category`, `quantity`, `unit_price`, "
             "`reorder_level`. Optional: `supplier`, `last_restocked`. Common aliases such as `qty`, `price` and "
             "`vendor` are understood.")
    upload = st.file_uploader("CSV file", type=["csv", "txt"])
    text, source = None, None
    if upload is not None:
        text, source = decode(upload.getvalue()), upload.name
    elif LEGACY_SAMPLE.exists() and st.checkbox("Use the bundled messy sample (legacy_import_sample.csv)"):
        text, source = read_text(LEGACY_SAMPLE), LEGACY_SAMPLE.name
    mode = st.radio("When a SKU already exists", ["skip", "update"], horizontal=True,
                    format_func=lambda m: "Keep the existing product" if m == "skip" else "Overwrite it with the file's values")
    if text is None:
        st.info("Choose a file to preview it before importing.")
        return

    st.caption(f"Raw preview of {source}")
    st.code("\n".join(text.splitlines()[:12]), language="text")
    if st.button("Import file", type="primary"):
        result = import_csv_text(inv, text, source=source, mode=mode, log_path=EXPORT_DIR / "import_errors.log")
        if not result.ok:
            st.error(result.headline())
            return
        m = st.columns(4)
        m[0].metric("Imported", result.imported)
        m[1].metric("Updated", result.updated)
        m[2].metric("Skipped (SKU exists)", result.skipped_existing)
        m[3].metric("Rejected", result.failed)
        if result.errors:
            st.warning("Some rows were rejected. The valid rows were imported.")
            st.dataframe(pd.DataFrame([{"Line": e.row, "SKU": e.sku, "Reason": e.reason} for e in result.errors]),
                         hide_index=True, use_container_width=True)
            st.download_button("Download error log", render_log(result), "import_errors.log", "text/plain")
        else:
            st.success("Every row imported cleanly.")


def page_alerts(inv: Inventory) -> None:
    warns = reports.warnings(inv.all(), inv.today)
    if not warns:
        st.success("No alerts. Every threshold is within limits.")
        return
    counts = {lvl: sum(w.level == lvl for w in warns) for lvl in LEVEL_COLORS}
    cols = st.columns(3)
    for col, (lvl, n) in zip(cols, counts.items()):
        col.metric(lvl.title(), n)
    levels = st.multiselect("Show", list(LEVEL_COLORS), default=["CRITICAL", "WARNING"])
    df = pd.DataFrame([{"Level": w.level, "SKU": w.sku, "Product": w.name, "Alert": w.code.replace("_", " ").title(),
                        "Detail": w.message} for w in warns if w.level in levels])
    if df.empty:
        st.info("No alerts at the selected levels.")
        return
    st.dataframe(style_column(df, "Level", LEVEL_COLORS), hide_index=True, use_container_width=True,
                 height=min(700, 38 + 35 * len(df)))
    st.caption("Thresholds: critical at or below 25% of the reorder level; overdue after "
               f"{reports.OVERDUE_DAYS} days without restock; quantity or price anomalies are statistical outliers.")


PAGE_FUNCS = {
    "Overview": page_overview, "Inventory": page_inventory, "Stock in and out": page_stock,
    "Products": page_products, "Reports": page_reports, "Bulk import": page_import, "Alerts": page_alerts,
}


def main() -> None:
    st.set_page_config(page_title="Inventory Stock Monitor", page_icon=":package:", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    try:
        inv = Inventory(Storage(DATA_PATH))
    except StorageError as exc:
        st.error(f"Cannot open the data file: {exc}")
        st.stop()
        return

    warns = reports.warnings(inv.all(), inv.today)
    n_crit = sum(w.level == "CRITICAL" for w in warns)
    n_warn = sum(w.level == "WARNING" for w in warns)

    with st.sidebar:
        st.markdown("### Inventory Stock Monitor")
        page = st.radio("Go to", PAGES, label_visibility="collapsed")
        chips = (f'<span class="chip crit">{n_crit} critical</span><span class="chip warn">{n_warn} warnings</span>'
                 if warns else '<span class="chip good">All clear</span>')
        st.markdown(chips, unsafe_allow_html=True)
        st.caption(f"Data file: {DATA_PATH}")
        with st.expander("Demo data"):
            st.caption("Replace everything with the 40-product demo dataset.")
            sure = st.checkbox("Yes, replace my inventory", key="restore_ok")
            if st.button("Restore demo data", disabled=not sure):
                products, movements = build_sample()
                Storage(DATA_PATH).save(products, movements)
                write_sample_files(DATA_PATH.parent, reset_live=False)
                flash("success", f"Loaded {len(products)} demo products.")
                rerun()

    st.title(page)
    show_flash()
    for note in inv.notes:
        st.warning(note)
    PAGE_FUNCS[page](inv)


main()
