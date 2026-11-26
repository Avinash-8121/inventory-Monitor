"""JSON persistence with atomic writes, backups, corruption recovery and a CSV mirror."""
from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from .models import PRODUCT_FIELDS, Movement, Product
from .validation import validate_record

SCHEMA_VERSION = 1


class StorageError(RuntimeError):
    """Raised when the data file cannot be read or written for a non-recoverable reason."""


class Storage:
    def __init__(self, path: str | os.PathLike = "data/inventory.json", mirror_csv: bool = True):
        self.path = Path(path)
        self.mirror_csv = mirror_csv

    # ----------------------------------------------------------------- load
    def load(self) -> tuple[list[Product], list[Movement], list[str]]:
        """Return (products, movements, notes). Never crashes on a bad or missing file."""
        notes: list[str] = []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            notes.append(f"No data file at {self.path}; started a fresh inventory.")
            self._write_template()
            return [], [], notes
        except json.JSONDecodeError as exc:
            notes.append(self._quarantine(f"not valid JSON ({exc.msg} at line {exc.lineno})"))
            self._write_template()
            return [], [], notes
        except UnicodeDecodeError:
            notes.append(self._quarantine("not readable as UTF-8 text"))
            self._write_template()
            return [], [], notes
        except OSError as exc:
            raise StorageError(f"Cannot read {self.path}: {exc}") from exc

        if isinstance(data, list):          # tolerate a bare list of products
            data = {"products": data, "movements": []}
        if not isinstance(data, dict) or not isinstance(data.get("products"), list):
            notes.append(self._quarantine("missing a 'products' list"))
            self._write_template()
            return [], [], notes

        products: list[Product] = []
        seen: set[str] = set()
        for i, raw in enumerate(data["products"], start=1):
            if not isinstance(raw, dict):
                notes.append(f"Skipped product #{i}: not an object.")
                continue
            clean, errors = validate_record(raw)
            if errors:
                notes.append(f"Skipped product #{i} ({raw.get('sku', '?')}): " + "; ".join(errors))
            elif clean["sku"] in seen:
                notes.append(f"Skipped product #{i}: duplicate SKU {clean['sku']}.")
            else:
                seen.add(clean["sku"])
                products.append(Product.from_dict(clean))

        movements: list[Movement] = []
        raw_moves = data.get("movements", [])
        for i, raw in enumerate(raw_moves if isinstance(raw_moves, list) else [], start=1):
            try:
                movements.append(Movement.from_dict(raw))
            except (KeyError, TypeError, ValueError):
                notes.append(f"Skipped malformed movement #{i}.")
        return products, movements, notes

    # ----------------------------------------------------------------- save
    def save(self, products: list[Product], movements: list[Movement]) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "products": [p.to_dict() for p in products],
            "movements": [m.to_dict() for m in movements],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                shutil.copy2(self.path, self.path.with_suffix(self.path.suffix + ".bak"))
            self._atomic_write(json.dumps(payload, indent=2, ensure_ascii=False))
            if self.mirror_csv:
                self.write_csv(products, self.path.with_suffix(".csv"))
        except OSError as exc:
            raise StorageError(f"Cannot write {self.path}: {exc}") from exc

    @staticmethod
    def write_csv(products: list[Product], path: str | os.PathLike) -> None:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=PRODUCT_FIELDS)
            writer.writeheader()
            for p in products:
                writer.writerow(p.to_dict())

    # -------------------------------------------------------------- helpers
    def _atomic_write(self, text: str) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".inv-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _write_template(self) -> None:
        try:
            self.save([], [])
        except StorageError:
            pass  # read-only location: the app still runs in memory

    def _quarantine(self, reason: str) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        try:
            os.replace(self.path, target)
            return f"Data file was {reason}. Saved the damaged copy as {target.name} and started a fresh inventory."
        except OSError:
            return f"Data file was {reason}. Started a fresh inventory."
