"""Terminal helpers: ANSI styling (auto-disabled when not a TTY), fixed-width tables, pagination."""
from __future__ import annotations

import math
import os
import sys
from typing import Callable, Sequence

_CODES = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m",
}
_enabled = False


def init(enabled: bool | None = None) -> None:
    global _enabled
    if enabled is None:
        enabled = (sys.stdout.isatty() and "NO_COLOR" not in os.environ
                   and os.environ.get("TERM") != "dumb")
    if enabled and os.name == "nt":
        os.system("")  # switches on ANSI handling in the Windows console
    _enabled = enabled


def style(text, *names: str) -> str:
    if not _enabled or not names:
        return str(text)
    return "".join(_CODES[n] for n in names) + str(text) + _CODES["reset"]


def ok(msg: str) -> None:
    print(style("  [+] ", "green", "bold") + msg)


def info(msg: str) -> None:
    print(style("  [i] ", "cyan") + msg)


def warn(msg: str) -> None:
    print(style("  [!] ", "yellow", "bold") + msg)


def error(msg: str) -> None:
    print(style(msg, "red"))


def heading(title: str) -> None:
    print()
    print(style(f"  {title}", "bold", "cyan"))
    print(style("  " + "-" * (len(title) + 2), "dim"))


STATUS_COLOR = {"OK": "green", "LOW": "yellow", "OUT": "red"}
LEVEL_COLOR = {"CRITICAL": "red", "WARNING": "yellow", "INFO": "cyan"}


def truncate(text: str, width: int) -> str:
    text = str(text)
    return text if len(text) <= width else text[: max(width - 3, 1)] + "..."


# column: (header, getter, width, align 'l'|'r', optional colour function(value)->tuple of style names)
Column = tuple


def table(rows: Sequence, columns: Sequence[Column]) -> list[str]:
    def cell(text: str, width: int, align: str, styles=()):
        t = truncate(text, width)
        padded = t.rjust(width) if align == "r" else t.ljust(width)
        return style(padded, *styles) if styles else padded

    header = " | ".join(style(h.rjust(w) if a == "r" else h.ljust(w), "bold")
                        for h, _, w, a, *_ in columns)
    rule = "-+-".join("-" * c[2] for c in columns)
    lines = ["  " + header, "  " + rule]
    for row in rows:
        parts = []
        for _, getter, width, align, *rest in columns:
            value = getter(row)
            styles = rest[0](value) if rest and rest[0] else ()
            parts.append(cell(str(value), width, align, styles))
        lines.append("  " + " | ".join(parts))
    return lines


def show_table(rows: Sequence, columns: Sequence[Column], page_size: int = 15,
               reader: Callable[[str], str] = input) -> None:
    total = len(rows)
    if total == 0:
        info("Nothing to show.")
        return
    pages = math.ceil(total / page_size)
    page = 0
    while True:
        chunk = rows[page * page_size:(page + 1) * page_size]
        print("\n".join(table(chunk, columns)))
        start, end = page * page_size + 1, page * page_size + len(chunk)
        print(style(f"  Showing {start}-{end} of {total}  (page {page + 1}/{pages})", "dim"))
        if pages == 1:
            return
        try:
            cmd = reader("  [Enter] next page   [p] previous   [q] back to menu: ").strip().lower()
        except EOFError:
            return
        if cmd == "q":
            return
        if cmd == "p":
            page = max(0, page - 1)
        else:
            page += 1
            if page >= pages:
                return
        print()


def bar(value: float, maximum: float, width: int = 24, fill: str = "#") -> str:
    n = 0 if maximum <= 0 else round(width * value / maximum)
    return fill * n + "." * (width - n)


def banner() -> None:
    lines = [
        "+--------------------------------------------------------------+",
        "|                                                              |",
        "|   INVENTORY STOCK MONITOR                                    |",
        "|   Track quantities. Catch low stock before it costs you.     |",
        "|                                                              |",
        "|   StudentLife Hack  |  Project #82  |  Small Business        |",
        "+--------------------------------------------------------------+",
    ]
    print()
    for line in lines:
        print(style("  " + line, "cyan"))
