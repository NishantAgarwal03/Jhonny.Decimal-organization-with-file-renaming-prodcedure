"""Scan and UI-logic regression tests. Stdlib only, temp folders only, no model, no Tk window.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import csv
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from johnny.ui import jd_ui_prototype as ui
from johnny.scan.scan_drive import scan_drive


class TempTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="johnny_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def touch(self, rel):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("x")
        return path


class ScanDrive(TempTree):
    def test_scan_lists_every_file_and_changes_nothing(self):
        files = ["a.PDF", "sub/b.txt", "sub/deep/c", ".hidden/d.jpg", "e.tar.gz"]
        for name in files:
            self.touch(name)
        out = os.path.join(tempfile.mkdtemp(prefix="johnny_scan_"), "scan.csv")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        with redirect_stdout(io.StringIO()):
            scan_drive(self.tmp, out)
        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), len(files))
        self.assertEqual(sorted(r["type"] for r in rows), sorted(["", ".pdf", ".txt", ".jpg", ".gz"]))
        self.assertTrue(all(not os.path.isabs(r["path"]) and ":" not in r["path"] for r in rows))
        self.assertEqual(sum(len(fs) for _, _, fs in os.walk(self.tmp)), len(files))


class UiHelpers(TempTree):
    def test_iter_files_skips_hidden_and_respects_scope(self):
        for name in ["a.txt", ".dot.txt", "sub/b.txt", ".hid/c.txt"]:
            self.touch(name)
        rel = lambda paths: sorted(os.path.relpath(p, self.tmp) for p in paths)
        self.assertEqual(rel(ui.iter_files(self.tmp, True)), ["a.txt", os.path.join("sub", "b.txt")])
        self.assertEqual(rel(ui.iter_files(self.tmp, False)), ["a.txt"])

    def test_safe_session_name(self):
        self.assertEqual(ui.safe_session_name(r"C:\Users\x\Downloads"), "session_Downloads")
        self.assertEqual(ui.safe_session_name("E:\\"), "session_E_Drive")

    def test_plan_text_flattens_separators_and_names_the_file_type(self):
        path = "Personal" + chr(92) + "Tax-Docs" + chr(92) + "ITR_2021.pdf"
        self.assertEqual(ui.plan_text(path), "ITR 2021 Personal Tax Docs pdf")          # the extension is part of the text by default
        self.assertEqual(ui.plan_text(path, types=None), "ITR 2021 Personal Tax Docs")  # the old text
        self.assertEqual(ui.plan_text(path, types="kind"), "ITR 2021 Personal Tax Docs pdf document")

    def test_unsorted_guard_boundaries(self):
        failed = lambda total, unsorted: ui.PrototypeApp._index_preview_failed(
            None, total, [("11.01_Tax", str(total - unsorted), ""), ("80.01_Unsorted_Miscellaneous", str(unsorted), "")]
        )
        self.assertFalse(failed(149, 0))
        self.assertTrue(failed(149, 1))     # floor(149/150) = 0 allowed
        self.assertFalse(failed(150, 1))    # floor(150/150) = 1 allowed
        self.assertTrue(failed(150, 2))
        self.assertFalse(failed(0, 0))

    def test_unsorted_guard_ignores_special_folders(self):
        rows = [("11.01_Tax", "3", ""), ("_Software_Projects", "5000", ""), ("_Temp_Safe_To_Remove", "900", ""), ("80.01_Unsorted_Miscellaneous", "0", "")]
        self.assertFalse(ui.PrototypeApp._index_preview_failed(None, 5903, rows))
        rows[3] = ("80.01_Unsorted_Miscellaneous", "1", "")           # 1 unsorted of 4 routed files: still blocked
        self.assertTrue(ui.PrototypeApp._index_preview_failed(None, 5904, rows))


if __name__ == "__main__":
    unittest.main()
