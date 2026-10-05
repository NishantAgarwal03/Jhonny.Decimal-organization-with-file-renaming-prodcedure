"""organizer.py end to end on temp folders, with a deterministic fake NLP (no model).

Layout per test:  <tmp>/jd/index.json  (JD root = index folder),  <tmp>/jd/inbox (target).
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import fakes
from johnny.organize import organizer
from johnny.rename import rename_engine
from johnny.core import utils
def tree(folder):
    out = {}
    for root, _, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            with open(p, "rb") as f:
                out[os.path.relpath(p, folder)] = hashlib.sha256(f.read()).hexdigest()
    return out


class OrganizerE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="johnny_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "jd")
        self.inbox = os.path.join(self.root, "inbox")
        os.makedirs(self.inbox)
        self.index = fakes.make_index(os.path.join(self.root, "index.json"))
        self.log = os.path.join(self.tmp, "undo.jsonl")
        for patcher in (
            mock.patch.object(utils, "UNDO_LOG", self.log),
            mock.patch.object(organizer, "NLPEngine", fakes.FakeNLP),
            mock.patch.object(rename_engine, "NLPEngine", fakes.FakeNLP),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def put(self, rel, content="x"):
        p = os.path.join(self.inbox, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
        return p

    def run_organizer(self, *flags, target=None):
        argv = ["organizer.py", target or self.inbox, "--index", self.index, *([] if "--dest" in flags else ["--dest", self.root]), *flags]
        with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()) as out:
            try:
                organizer.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def jd(self):
        return {k: v for k, v in tree(self.root).items() if not k.startswith("index")}

    def test_engines_are_built_with_the_index_profiles(self):
        with open(self.index, encoding="utf-8") as f:
            data = json.load(f)
        data["profiles"] = {"Tax": {"description": "Tax filings."}}
        with open(self.index, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.put("tax_a.txt", "tax")
        fakes.FakeNLP.instances.clear()
        self.run_organizer("--read-only")                    # builds the organizer engine and the renamer's engine
        self.assertGreaterEqual(len(fakes.FakeNLP.instances), 2)
        self.assertTrue(all(e.profiles.get("Tax") == {"description": "Tax filings."} for e in fakes.FakeNLP.instances))

    def test_read_only_moves_nothing(self):
        self.put("tax_2021.txt"); self.put("study_notes.txt")
        before = tree(self.root)
        code, out = self.run_organizer("--read-only", "--fast-seed")
        self.assertEqual(code, 0)
        self.assertEqual(tree(self.root), before)
        self.assertIn("[Read-Only] Would route tax_2021.txt to 'Tax'", out)
        self.assertFalse(os.path.exists(self.log))

    def test_fast_seed_apply_routes_preserves_bytes_and_is_undoable(self):
        names = {"tax_2021.txt": "T1", "tax_return.txt": "T2", "study_notes.txt": "S1"}
        for n, c in names.items():
            self.put(n, c)
        for i in range(150):
            self.put(f"tax_filler_{i}.txt", str(i))
        before = tree(self.inbox)
        code, _ = self.run_organizer("--fast-seed")
        self.assertEqual(code, 0)
        moved = {os.path.basename(k): v for k, v in self.jd().items()}
        self.assertEqual(len(self.jd()), len(before))                           # nothing lost
        self.assertEqual(sorted(moved.values()), sorted(before.values()))       # bytes intact
        self.assertTrue(os.path.exists(os.path.join(self.root, "11.01_Tax", "tax_2021.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "12.01_Study", "study_notes.txt")))
        self.assertFalse(any(os.path.isfile(os.path.join(self.inbox, n)) for n in names))
        utils.undo_renames(dry_run=False)
        self.assertEqual(tree(self.inbox), before)

    def test_unsorted_guard_blocks_apply_and_moves_nothing(self):
        for i in range(9):
            self.put(f"tax_{i}.txt")
        self.put("random_thing.txt")               # 1 unsorted of 10 -> allowed 0
        before = tree(self.root)
        code, out = self.run_organizer("--fast-seed")
        self.assertEqual(code, 2)
        self.assertIn("Apply blocked", out)
        self.assertEqual(tree(self.root), before)
        self.assertFalse(os.path.exists(self.log))

    def test_organized_folders_and_dotfiles_are_left_alone(self):
        self.put("11.01_Tax/already.txt"); self.put(".hidden.txt", "h"); self.put("tax_a.txt")
        for i in range(150):
            self.put(f"tax_f{i}.txt")
        self.run_organizer("--fast-seed")
        self.assertTrue(os.path.exists(os.path.join(self.inbox, "11.01_Tax", "already.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.inbox, ".hidden.txt")))

    def test_full_mode_sorts_no_text_files_and_renames_vague_names(self):
        self.put("scan_001.txt", "tax return acknowledgment 2021")
        self.put("photo.jpg", "binary-ish")
        for i in range(300):                                                    # allows 2 Unsorted: the two files whose names say nothing
            self.put(f"note_{i}.txt", "tax filing")
        before_jpg = tree(self.inbox)["photo.jpg"]
        code, out = self.run_organizer()
        self.assertEqual(code, 0)
        self.assertNotIn("Skipping", out)                                       # a file without text is still sorted, on name + path
        self.assertNotIn("photo.jpg", os.listdir(self.inbox))
        self.assertIn(before_jpg, [v for k, v in self.jd().items() if k.endswith("photo.jpg")])   # moved, bytes intact
        self.assertNotIn("scan_001.txt", os.listdir(self.inbox))                # renamed ...
        self.assertFalse(any(k.startswith("11.01_Tax") and "scan_001" in k for k in self.jd()))  # safeguard A: a nameless file is held, text cannot rescue it
        self.assertTrue(any(k.startswith("11.01_Tax") for k in self.jd()))      # the named files are routed
        with open(self.log, encoding="utf-8") as f:
            self.assertGreater(len(f.read().splitlines()), 300)                 # every step logged

    def test_missing_index_stops_before_touching_anything(self):
        self.put("tax_a.txt")
        before = tree(self.root)
        self.index = os.path.join(self.root, "typo.json")
        with self.assertRaises(FileNotFoundError):
            self.run_organizer("--fast-seed")
        self.assertEqual(tree(self.root), before)
        self.assertFalse(os.path.exists(self.index))

    def test_files_land_in_dest_not_beside_the_index(self):
        """Audit A3: the sorted folders go to --dest; the index's folder is never the silent destination."""
        elsewhere = os.path.join(self.tmp, "elsewhere")
        dest = os.path.join(self.tmp, "sorted_here")
        os.makedirs(elsewhere)
        for name in ["tax_a.txt"] + [f"tax_f{i}.txt" for i in range(150)]:
            with open(os.path.join(elsewhere, name), "w") as f:
                f.write("x")
        code, out = self.run_organizer("--fast-seed", "--dest", dest, target=elsewhere)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(os.path.join(dest, "11.01_Tax", "tax_a.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "11.01_Tax")))
        self.assertIn(f"Sorted files go to: {dest}", out)
        self.assertIn("Done:", out)

    def test_without_dest_the_default_is_a_dated_desktop_folder_and_read_only_creates_nothing(self):
        fake_home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(fake_home, "Desktop"))
        self.put("tax_a.txt")
        with mock.patch.object(utils.Path, "home", return_value=utils.Path(fake_home)):
            expected = utils.default_sorted_root("2026-10-05")
            self.assertEqual(expected, os.path.join(fake_home, "Desktop", "Sorted JD_2026-10-05"))
            with mock.patch.object(organizer, "default_sorted_root", lambda: expected):
                code, out = self.run_organizer("--read-only", "--dest", "")
        self.assertEqual(code, 0)
        self.assertIn(expected, out)
        self.assertFalse(os.path.exists(expected))


if __name__ == "__main__":
    unittest.main()
