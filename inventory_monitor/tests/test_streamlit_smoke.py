"""Smoke test for streamlit_app.py.

* With Streamlit installed: uses Streamlit's official AppTest harness.
* Without it: runs the script against a stub of the Streamlit/Altair API so that every page's
  code path (including form submissions) executes and any NameError/TypeError/logic bug surfaces.
"""
import os
import runpy
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["Overview", "Inventory", "Stock in and out", "Products", "Reports", "Bulk import", "Alerts"]

try:
    import streamlit  # noqa: F401
    HAVE_STREAMLIT = not isinstance(sys.modules.get("streamlit"), MagicMock)
except ImportError:
    HAVE_STREAMLIT = False


def make_stub(page: str, submit: bool):
    st = MagicMock(name="streamlit")
    st.session_state = {}
    st.columns.side_effect = lambda spec, **kw: [st] * (spec if isinstance(spec, int) else len(spec))
    st.tabs.side_effect = lambda labels: [MagicMock() for _ in labels]
    st.radio.side_effect = lambda label, options, **kw: page if label == "Go to" else options[0]
    st.selectbox.side_effect = lambda label, options, **kw: list(options)[0]
    st.multiselect.side_effect = lambda label, options, **kw: kw.get("default", [])
    st.text_input.side_effect = lambda label, **kw: kw.get("value", "")
    st.number_input.side_effect = lambda label, **kw: kw.get("value", 0)
    st.date_input.side_effect = lambda label, **kw: date.today()
    st.checkbox.return_value = True
    st.file_uploader.return_value = None
    st.button.return_value = submit
    st.form_submit_button.return_value = submit
    st.stop.side_effect = RuntimeError("st.stop called")
    st.sidebar = st
    return st


@unittest.skipIf(HAVE_STREAMLIT, "real Streamlit present; AppTest used instead")
class StubbedStreamlitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        shutil.copytree(ROOT / "data", tmp / "data")
        os.environ["INVENTORY_DATA"] = str(tmp / "data" / "inventory.json")
        os.environ["INVENTORY_EXPORTS"] = str(tmp / "exports")
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("INVENTORY_DATA", "INVENTORY_EXPORTS")])
        self._saved = {k: sys.modules.get(k) for k in ("streamlit", "altair")}
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def run_page(self, page, submit):
        st = make_stub(page, submit)
        sys.modules["streamlit"] = st
        sys.modules["altair"] = MagicMock(name="altair")
        runpy.run_path(str(ROOT / "streamlit_app.py"), run_name="streamlit_app")
        return st

    def test_every_page_renders(self):
        for page in PAGES:
            with self.subTest(page=page):
                st = self.run_page(page, submit=False)
                st.title.assert_called_with(page)

    def test_every_page_with_buttons_pressed(self):
        for page in PAGES:
            with self.subTest(page=page):
                self.run_page(page, submit=True)

    def test_sale_form_updates_data(self):
        from core.inventory import Inventory
        from core.storage import Storage
        path = Path(os.environ["INVENTORY_DATA"])
        before = Inventory(Storage(path)).products["ELC-001"].quantity
        self.run_page("Stock in and out", submit=True)
        sold_from = sorted(Inventory(Storage(path)).products)[0]   # stub picks the first SKU
        self.assertEqual(sold_from, "APP-001")
        self.assertNotEqual(Inventory(Storage(path)).products["APP-001"].quantity,
                            sample_qty("APP-001"))
        self.assertEqual(Inventory(Storage(path)).products["ELC-001"].quantity, before)

    def test_add_form_shows_validation_errors(self):
        st = self.run_page("Products", submit=True)      # blank SKU/name/category -> errors shown, nothing saved
        self.assertTrue(st.error.called)


def sample_qty(sku):
    from core.sample_data import build_sample
    return next(p.quantity for p in build_sample()[0] if p.sku == sku)


@unittest.skipUnless(HAVE_STREAMLIT, "Streamlit not installed")
class RealStreamlitTests(unittest.TestCase):
    def test_pages_render_without_exceptions(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(ROOT / "data", Path(tmp) / "data")
            os.environ["INVENTORY_DATA"] = str(Path(tmp) / "data" / "inventory.json")
            os.environ["INVENTORY_EXPORTS"] = str(Path(tmp) / "exports")
            try:
                for page in PAGES:
                    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=60).run()
                    at.sidebar.radio[0].set_value(page).run()
                    self.assertFalse(at.exception, f"{page}: {at.exception}")
            finally:
                os.environ.pop("INVENTORY_DATA", None)
                os.environ.pop("INVENTORY_EXPORTS", None)


if __name__ == "__main__":
    unittest.main()
