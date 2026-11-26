"""Executive summary, threshold warnings and Markdown / text / CSV / chart exports."""
from __future__ import annotations

import csv
import io
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from .models import Movement, Product, StockStatus

OVERDUE_DAYS = 90        # LOW/OUT items not restocked for this long are "overdue"
STALE_DAYS = 180         # healthy items untouched for this long get an info flag
CRITICAL_RATIO = 0.25    # quantity at or below 25% of the reorder level is critical
ANOMALY_IQR_FACTOR = 3.0
ANOMALY_MEDIAN_FACTOR = 20.0

SEVERITY = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}


# ------------------------------------------------------------------ warnings
@dataclass
class Warn:
    level: str      # CRITICAL | WARNING | INFO
    code: str
    sku: str
    name: str
    message: str


def _upper_fence(values: list[float]) -> float | None:
    if len(values) < 8:
        return None
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    # Must be a statistical outlier AND far above the typical value, so skewed
    # but legitimate data (e.g. 500 printing credits) is not flagged.
    return max(q3 + ANOMALY_IQR_FACTOR * (q3 - q1), ANOMALY_MEDIAN_FACTOR * statistics.median(values))


def warnings(products: Iterable[Product], today: date) -> list[Warn]:
    prods = list(products)
    out: list[Warn] = []
    qty_fence = _upper_fence([p.quantity for p in prods])
    price_fence = _upper_fence([p.unit_price for p in prods])

    for p in prods:
        def add(level, code, msg):
            out.append(Warn(level, code, p.sku, p.name, msg))

        days = p.days_since_restock(today)
        if p.status is StockStatus.OUT:
            add("CRITICAL", "OUT_OF_STOCK", "Out of stock. Reorder now.")
        elif p.status is StockStatus.LOW:
            if p.quantity <= p.reorder_level * CRITICAL_RATIO:
                add("CRITICAL", "CRITICAL_LOW",
                    f"Only {p.quantity} left (reorder level {p.reorder_level}); under 25% of the threshold.")
            else:
                add("WARNING", "LOW_STOCK",
                    f"{p.quantity} left, at or below the reorder level of {p.reorder_level}.")
        if p.status is not StockStatus.OK and days is not None and days > OVERDUE_DAYS:
            add("WARNING", "OVERDUE_RESTOCK", f"Not restocked for {days} days (limit {OVERDUE_DAYS}).")
        elif p.status is StockStatus.OK and days is not None and days > STALE_DAYS:
            add("INFO", "STALE_RESTOCK", f"Healthy stock but untouched for {days} days.")
        if qty_fence is not None and p.quantity > qty_fence:
            add("WARNING", "ANOMALY_QUANTITY",
                f"Unusually high quantity ({p.quantity:,}); check for a data-entry error.")
        if price_fence is not None and p.unit_price > price_fence:
            add("WARNING", "ANOMALY_PRICE",
                f"Unusually high unit price ({p.unit_price:,.2f}); verify it.")
        if p.unit_price == 0:
            add("WARNING", "ANOMALY_PRICE", "Unit price is 0.00; verify it.")

    out.sort(key=lambda w: (SEVERITY[w.level], w.sku))
    return out


# ------------------------------------------------------------------- summary
def movement_trend(movements: Iterable[Movement], today: date, window: int = 30) -> dict:
    start = today - timedelta(days=window - 1)
    prev_start = start - timedelta(days=window)
    daily = {start + timedelta(days=i): [0, 0] for i in range(window)}
    sold = restocked = prev_sold = 0
    per_sku: Counter = Counter()

    for m in movements:
        try:
            d = datetime.fromisoformat(m.timestamp).date()
        except ValueError:
            continue
        if m.kind == "sale":
            units = abs(m.delta)
            if start <= d <= today:
                sold += units
                daily[d][0] += units
                per_sku[m.sku] += units
            elif prev_start <= d < start:
                prev_sold += units
        elif m.kind == "restock" and start <= d <= today:
            restocked += m.delta
            daily[d][1] += m.delta

    if prev_sold == 0:
        change, direction = None, ("new" if sold else "flat")
    else:
        change = round((sold - prev_sold) / prev_sold * 100, 1)
        direction = "up" if change > 5 else "down" if change < -5 else "flat"
    return {
        "window_days": window, "units_sold": sold, "units_restocked": restocked,
        "net_change": restocked - sold, "prev_units_sold": prev_sold,
        "change_pct": change, "direction": direction,
        "daily": [(d.isoformat(), v[0], v[1]) for d, v in sorted(daily.items())],
        "top_sellers": per_sku.most_common(5),
    }


def summary(products: Iterable[Product], movements: Iterable[Movement],
            today: date, window: int = 30) -> dict:
    prods = list(products)
    n = len(prods)
    status_counts = {s.value: 0 for s in StockStatus}
    cats: dict[str, dict] = defaultdict(lambda: {"products": 0, "units": 0, "value": 0.0, "attention": 0})
    for p in prods:
        status_counts[p.status.value] += 1
        c = cats[p.category]
        c["products"] += 1
        c["units"] += p.quantity
        c["value"] += p.stock_value
        c["attention"] += p.status is not StockStatus.OK

    by_category = sorted(
        ({"category": k, **v, "value": round(v["value"], 2)} for k, v in cats.items()),
        key=lambda r: r["value"], reverse=True)
    prices = [p.unit_price for p in prods]
    qtys = [p.quantity for p in prods]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "as_of": today.isoformat(),
        "total_products": n,
        "total_units": sum(qtys),
        "total_value": round(sum(p.stock_value for p in prods), 2),
        "avg_price": round(statistics.fmean(prices), 2) if n else 0.0,
        "avg_quantity": round(statistics.fmean(qtys), 1) if n else 0.0,
        "median_quantity": statistics.median(qtys) if n else 0,
        "status_counts": status_counts,
        "health_pct": round(status_counts["OK"] / n * 100, 1) if n else 0.0,
        "by_category": by_category,
        "top_value": [(p.sku, p.name, p.stock_value)
                      for p in sorted(prods, key=lambda p: p.stock_value, reverse=True)[:5]],
        "lowest_stock": [(p.sku, p.name, p.quantity, p.reorder_level)
                         for p in sorted(prods, key=lambda p: (p.quantity / max(p.reorder_level, 1), p.sku))[:5]],
        "trend": movement_trend(movements, today, window),
    }


# ------------------------------------------------------------------- renderers
def _money(x: float) -> str:
    return f"{x:,.2f}"


def _trend_sentence(t: dict) -> str:
    base = f"{t['units_sold']:,} units sold and {t['units_restocked']:,} restocked in the last {t['window_days']} days"
    if t["change_pct"] is None:
        return base + "; no earlier sales to compare against."
    return base + f"; sales are {t['direction']} {abs(t['change_pct'])}% on the previous {t['window_days']} days."


def to_markdown(s: dict, warns: list[Warn], products: list[Product], today: date) -> str:
    sc = s["status_counts"]
    L = [f"# Inventory Stock Report", "", f"*As of {s['as_of']} (generated {s['generated_at']})*", "",
         "## Overview", "", "| Metric | Value |", "|---|---:|",
         f"| Products | {s['total_products']:,} |", f"| Units on hand | {s['total_units']:,} |",
         f"| Stock value | {_money(s['total_value'])} |", f"| Average unit price | {_money(s['avg_price'])} |",
         f"| Average quantity per product | {s['avg_quantity']:,} |",
         f"| Median quantity per product | {s['median_quantity']:,} |",
         f"| Healthy products | {s['health_pct']}% |", "",
         "## Stock status", "", "| Status | Products |", "|---|---:|",
         f"| OK | {sc['OK']} |", f"| LOW | {sc['LOW']} |", f"| OUT | {sc['OUT']} |", "",
         "## By category", "", "| Category | Products | Units | Value | Needing attention |", "|---|---:|---:|---:|---:|"]
    L += [f"| {c['category']} | {c['products']} | {c['units']:,} | {_money(c['value'])} | {c['attention']} |"
          for c in s["by_category"]]
    L += ["", "## Highest stock value", "", "| SKU | Product | Value |", "|---|---|---:|"]
    L += [f"| {a} | {b} | {_money(c)} |" for a, b, c in s["top_value"]]
    L += ["", "## Closest to running out", "", "| SKU | Product | Qty | Reorder level |", "|---|---|---:|---:|"]
    L += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in s["lowest_stock"]]
    t = s["trend"]
    L += ["", "## Trend", "", _trend_sentence(t), ""]
    if t["top_sellers"]:
        L += ["Top sellers: " + ", ".join(f"{k} ({v})" for k, v in t["top_sellers"]) + ".", ""]
    L += ["## Alerts", ""]
    if warns:
        L += ["| Level | SKU | Code | Detail |", "|---|---|---|---|"]
        L += [f"| {w.level} | {w.sku} | {w.code} | {w.message} |" for w in warns]
    else:
        L.append("No alerts. All thresholds are within limits.")
    L += ["", "## Full inventory", "", "| SKU | Name | Category | Qty | Reorder | Price | Status | Last restocked |",
          "|---|---|---|---:|---:|---:|---|---|"]
    L += [f"| {p.sku} | {p.name} | {p.category} | {p.quantity} | {p.reorder_level} | "
          f"{_money(p.unit_price)} | {p.status.value} | {p.last_restocked} |"
          for p in sorted(products, key=lambda p: p.sku)]
    return "\n".join(L) + "\n"


def to_text_audit(s: dict, warns: list[Warn], products: list[Product],
                  movements: list[Movement], today: date) -> str:
    bar = "=" * 78
    sc = s["status_counts"]
    L = [bar, "INVENTORY STOCK MONITOR - AUDIT LOG", f"As of {s['as_of']}   Generated {s['generated_at']}", bar, "",
         "SUMMARY",
         f"  Products ........ {s['total_products']:,}", f"  Units on hand ... {s['total_units']:,}",
         f"  Stock value ..... {_money(s['total_value'])}", f"  Avg unit price .. {_money(s['avg_price'])}",
         f"  Avg quantity .... {s['avg_quantity']:,}",
         f"  Status .......... OK {sc['OK']} | LOW {sc['LOW']} | OUT {sc['OUT']}  ({s['health_pct']}% healthy)",
         f"  Trend ........... {_trend_sentence(s['trend'])}", "",
         f"ALERTS ({len(warns)})"]
    L += [f"  [{w.level:<8}] {w.sku:<9} {w.code:<17} {w.message}" for w in warns] or ["  None."]
    L += ["", "INVENTORY", f"  {'SKU':<9} {'NAME':<34} {'QTY':>6} {'REORD':>6} {'PRICE':>10}  STATUS  RESTOCKED"]
    for p in sorted(products, key=lambda p: p.sku):
        name = p.name if len(p.name) <= 34 else p.name[:31] + "..."
        L.append(f"  {p.sku:<9} {name:<34} {p.quantity:>6} {p.reorder_level:>6} {p.unit_price:>10,.2f}  "
                 f"{p.status.value:<6}  {p.last_restocked}")
    recent = list(reversed(sorted(movements, key=lambda m: m.timestamp)))[:50]
    L += ["", f"RECENT MOVEMENTS (latest {len(recent)} of {len(movements)})"]
    L += [f"  {m.timestamp}  {m.sku:<9} {m.kind:<10} {m.delta:>+7}  -> {m.resulting_qty:>6}  {m.note}"
          for m in recent] or ["  None."]
    L += ["", bar, "END OF REPORT", bar]
    return "\n".join(L) + "\n"


def to_csv_report(products: Iterable[Product], today: date) -> str:
    buf = io.StringIO()
    cols = ["sku", "name", "category", "quantity", "reorder_level", "unit_price", "stock_value",
            "status", "supplier", "last_restocked", "days_since_restock"]
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(cols)
    for p in sorted(products, key=lambda p: p.sku):
        writer.writerow([p.sku, p.name, p.category, p.quantity, p.reorder_level, f"{p.unit_price:.2f}",
                         f"{p.stock_value:.2f}", p.status.value, p.supplier, p.last_restocked,
                         "" if p.days_since_restock(today) is None else p.days_since_restock(today)])
    return buf.getvalue()


def to_dataframe(products: Iterable[Product], today: date):
    """pandas DataFrame for the GUI (imported lazily so the core stays stdlib-only)."""
    import pandas as pd
    rows = [{
        "SKU": p.sku, "Name": p.name, "Category": p.category, "Qty": p.quantity,
        "Reorder level": p.reorder_level, "Unit price": p.unit_price, "Stock value": p.stock_value,
        "Status": p.status.value, "Supplier": p.supplier, "Last restocked": p.last_restocked,
        "Days since restock": p.days_since_restock(today),
    } for p in products]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------- charts
def build_dashboard_figure(s: dict, products: list[Product]):
    """2x2 Matplotlib dashboard figure. Raises ImportError if matplotlib is missing."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"OK": "#2F6F4E", "LOW": "#C77D0A", "OUT": "#B42318"}
    fig, ax = plt.subplots(2, 2, figsize=(13, 8.5))
    fig.suptitle(f"Inventory Stock Monitor - {s['as_of']}", fontsize=15, fontweight="bold")

    sc = s["status_counts"]
    ax[0][0].bar(list(sc), list(sc.values()), color=[colors[k] for k in sc])
    ax[0][0].set_title("Products by stock status")
    for i, v in enumerate(sc.values()):
        ax[0][0].text(i, v, str(v), ha="center", va="bottom")

    cats = s["by_category"][::-1]
    ax[0][1].barh([c["category"] for c in cats], [c["value"] for c in cats], color="#0F766E")
    ax[0][1].set_title("Stock value by category")

    risky = sorted((p for p in products if p.status is not StockStatus.OK),
                   key=lambda p: p.quantity / max(p.reorder_level, 1))[:10][::-1]
    if risky:
        y = range(len(risky))
        ax[1][0].barh(y, [p.reorder_level for p in risky], color="#D5DEDE", label="Reorder level")
        ax[1][0].barh(y, [p.quantity for p in risky], color=[colors[p.status.value] for p in risky], height=0.45,
                      label="On hand")
        ax[1][0].set_yticks(list(y))
        ax[1][0].set_yticklabels([p.sku for p in risky])
        ax[1][0].legend(loc="upper right")
    else:
        ax[1][0].text(0.5, 0.5, "No low-stock items", ha="center", va="center")
        ax[1][0].set_axis_off()
    ax[1][0].set_title("Items below threshold")

    daily = s["trend"]["daily"]
    days = [d[0][5:] for d in daily]
    ax[1][1].plot(days, [d[1] for d in daily], color="#B42318", label="Sold")
    ax[1][1].plot(days, [d[2] for d in daily], color="#2F6F4E", label="Restocked")
    ax[1][1].set_xticks(range(0, len(days), max(1, len(days) // 6)))
    ax[1][1].set_xticklabels(days[::max(1, len(days) // 6)], rotation=30)
    ax[1][1].set_title(f"Units moved per day (last {s['trend']['window_days']} days)")
    ax[1][1].legend()

    for a in ax.flat:
        a.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return fig


def save_dashboard_chart(s: dict, products: list[Product], path: Path) -> Path:
    import matplotlib.pyplot as plt
    fig = build_dashboard_figure(s, products)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------- export
def export_reports(inv, out_dir: str | Path, formats: Iterable[str] = ("md", "txt", "csv", "png")) -> dict:
    """Write the requested reports. Returns {'files': [Path...], 'skipped': [reason...]}."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    today = inv.today
    prods = sorted(inv.all(), key=lambda p: p.sku)
    s = summary(prods, inv.movements, today)
    warns = warnings(prods, today)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    files, skipped = [], []

    for fmt in formats:
        if fmt == "md":
            path = out / f"stock_report_{stamp}.md"
            path.write_text(to_markdown(s, warns, prods, today), encoding="utf-8")
        elif fmt == "txt":
            path = out / f"audit_log_{stamp}.txt"
            path.write_text(to_text_audit(s, warns, prods, inv.movements, today), encoding="utf-8")
        elif fmt == "csv":
            path = out / f"stock_report_{stamp}.csv"
            path.write_text(to_csv_report(prods, today), encoding="utf-8", newline="")
        elif fmt == "png":
            path = out / f"dashboard_{stamp}.png"
            try:
                save_dashboard_chart(s, prods, path)
            except ImportError:
                skipped.append("Chart skipped: install matplotlib (pip install matplotlib).")
                continue
        else:
            skipped.append(f"Unknown format {fmt!r}.")
            continue
        files.append(path)
    return {"files": files, "skipped": skipped}
