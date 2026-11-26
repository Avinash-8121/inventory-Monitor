"""Defensive validation: pure validators plus loop-until-valid prompt wrappers.

Validators take (value, today) and return a clean value or raise ValidationError.
Messages read as "<Label> <message>", e.g. "Quantity cannot be negative".
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable

MAX_QTY = 1_000_000
MAX_REORDER = 100_000
MAX_PRICE = 1_000_000.00
NAME_MAX, CATEGORY_MAX, SUPPLIER_MAX = 80, 30, 60
MIN_DATE = date(2000, 1, 1)

SKU_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,19}$")
CATEGORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &/'\-]*$")
_THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+$")


class ValidationError(ValueError):
    """Raised when a value breaks a domain rule."""


class Cancelled(Exception):
    """Raised when the user types /cancel at a prompt."""


# ----------------------------------------------------------------- validators
def _clean_text(value, *, required: bool, max_len: int) -> str:
    text = " ".join(("" if value is None else str(value)).split())
    if not text:
        if required:
            raise ValidationError("is required and cannot be blank")
        return ""
    if not text.isprintable():
        raise ValidationError("contains non-printable characters")
    if len(text) > max_len:
        raise ValidationError(f"is too long ({len(text)} characters; maximum is {max_len})")
    return text


def _to_int(value) -> int:
    if isinstance(value, bool):
        raise ValidationError("must be a whole number")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        raise ValidationError(f"must be a whole number (got {value!r})")
    raw = "" if value is None else str(value).strip()
    if not raw:
        raise ValidationError("is required and cannot be blank")
    text = raw.replace(",", "") if _THOUSANDS_RE.match(raw) else raw
    if not re.fullmatch(r"[+-]?\d+", text):
        raise ValidationError(f"must be a whole number (got {raw!r})")
    return int(text)


def validate_sku(value, today=None) -> str:
    text = _clean_text(value, required=True, max_len=40).upper()
    if not SKU_RE.match(text):
        raise ValidationError(
            f"must be 3-20 characters: letters, digits or hyphens, e.g. STN-001 (got {text!r})")
    return text


def validate_name(value, today=None) -> str:
    return _clean_text(value, required=True, max_len=NAME_MAX)


def validate_category(value, today=None) -> str:
    text = _clean_text(value, required=True, max_len=CATEGORY_MAX)
    if not CATEGORY_RE.match(text):
        raise ValidationError("may only contain letters, digits, spaces and & / ' -")
    return text


def validate_supplier(value, today=None) -> str:
    return _clean_text(value, required=False, max_len=SUPPLIER_MAX)


def validate_quantity(value, today=None, minimum: int = 0, maximum: int = MAX_QTY) -> int:
    n = _to_int(value)
    if n < minimum:
        raise ValidationError("cannot be negative" if minimum == 0
                              else f"must be at least {minimum:,} (got {n:,})")
    if n > maximum:
        raise ValidationError(f"is too large (maximum is {maximum:,}; got {n:,})")
    return n


def validate_positive_quantity(value, today=None) -> int:
    return validate_quantity(value, today, minimum=1)


def validate_reorder_level(value, today=None) -> int:
    return validate_quantity(value, today, minimum=0, maximum=MAX_REORDER)


def validate_price(value, today=None) -> float:
    if isinstance(value, bool):
        raise ValidationError("must be a number like 12.99")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValidationError("must be a finite number")
        d = Decimal(str(round(value, 2) if isinstance(value, float) else value))
    else:
        raw = "" if value is None else str(value).strip()
        if not raw:
            raise ValidationError("is required and cannot be blank")
        try:
            d = Decimal(raw)
        except InvalidOperation:
            raise ValidationError(f"must be a number like 12.99 (got {raw!r})") from None
        if not d.is_finite():
            raise ValidationError(f"must be a number like 12.99 (got {raw!r})")
    if d < 0:
        raise ValidationError("cannot be negative")
    if d > Decimal(str(MAX_PRICE)):
        raise ValidationError(f"is too large (maximum is {MAX_PRICE:,.2f})")
    if d.as_tuple().exponent < -2:
        raise ValidationError("can have at most 2 decimal places")
    return float(d.quantize(Decimal("0.01")))


def validate_date(value, today: date | None = None) -> str:
    """Optional ISO date (YYYY-MM-DD). Blank returns ''. Future dates are rejected."""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        value = value.isoformat()
    raw = "" if value is None else str(value).strip()
    if not raw:
        return ""
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(f"must be a real date in YYYY-MM-DD format (got {raw!r})") from None
    if parsed < MIN_DATE:
        raise ValidationError(f"cannot be before {MIN_DATE.isoformat()}")
    if parsed > (today or date.today()):
        raise ValidationError("cannot be in the future")
    return parsed.isoformat()


FIELD_VALIDATORS: dict[str, Callable] = {
    "sku": validate_sku,
    "name": validate_name,
    "category": validate_category,
    "quantity": validate_quantity,
    "unit_price": validate_price,
    "reorder_level": validate_reorder_level,
    "supplier": validate_supplier,
    "last_restocked": validate_date,
    "created_at": validate_date,
}


def validate_record(raw: dict, today: date | None = None, partial: bool = False):
    """Validate a whole record. Returns (clean_dict, errors). All errors are collected."""
    clean, errors = {}, []
    for field, fn in FIELD_VALIDATORS.items():
        if partial and field not in raw:
            continue
        try:
            clean[field] = fn(raw.get(field, ""), today)
        except ValidationError as exc:
            errors.append(f"{field} {exc}")
    return clean, errors


# ------------------------------------------------------------ prompt wrappers
def prompt_field(label: str, validator: Callable, *, default=None, hint: str = "",
                 reader: Callable[[str], str] = input, printer: Callable[[str], None] = print):
    """Ask until the validator accepts the answer. '/cancel' aborts. EOFError propagates."""
    suffix = f" [{default}]" if default not in (None, "") else ""
    hint_txt = f" ({hint})" if hint else ""
    while True:
        raw = reader(f"  {label}{hint_txt}{suffix}: ").strip()
        if raw.lower() in {"/cancel", "/q"}:
            raise Cancelled()
        if raw == "" and default is not None:
            raw = str(default)
        try:
            return validator(raw, date.today())
        except ValidationError as exc:
            printer(f"  [x] {label} {exc}. Try again, or type /cancel.")


def prompt_choice(label: str, options: list[str], *, default: str | None = None,
                  reader: Callable[[str], str] = input,
                  printer: Callable[[str], None] = print) -> str:
    lowered = {o.lower(): o for o in options}
    while True:
        raw = reader(f"  {label} ({'/'.join(options)})"
                     + (f" [{default}]" if default else "") + ": ").strip()
        if raw.lower() in {"/cancel", "/q"}:
            raise Cancelled()
        if raw == "" and default:
            return default
        if raw.lower() in lowered:
            return lowered[raw.lower()]
        printer(f"  [x] Choose one of: {', '.join(options)}.")


def prompt_yes_no(label: str, *, default: bool = False,
                  reader: Callable[[str], str] = input,
                  printer: Callable[[str], None] = print) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = reader(f"  {label} [{hint}]: ").strip().lower()
        if raw == "":
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        printer("  [x] Please answer y or n.")
