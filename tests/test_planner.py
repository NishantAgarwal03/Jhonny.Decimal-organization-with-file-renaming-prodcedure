"""planner.py (junk, software units, managed folders), Router.route_special, and the organizer integration.

Temp folders only; a fake classifier stands in for the embedding model.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import fakes
from johnny.organize import organizer
from johnny.core import planner
from johnny.rename import rename_engine
from johnny.core import utils
from johnny.core.index_manager import IndexManager
from johnny.organize.router import Router


def tree(folder, skip_notes=True):
    out = {}
    for root, _, files in os.walk(folder):
        for name in files:
            if skip_notes and name == "_README.txt":
                continue
            p = os.path.join(root, name)
            with open(p, "rb") as f:
                out[os.path.relpath(p, folder)] = f.read()
    return out


class PlannerRules(unittest.TestCase):
    def test_junk_tiers(self):
        for name in ("Thumbs.db", "desktop.ini", ".DS_Store", "~$report.docx", "x.tmp", "mod.pyc"):
            self.assertEqual(planner.junk_tier(name), "safe", name)
        for name in ("old_notes.bak", "debug.log", "movie.part", "x.crdownload", "core.dmp"):
            self.assertEqual(planner.junk_tier(name), "review", name)
        for name in ("report.pdf", "photo.jpg", "setup.exe", "library.dll", "notes.txt"):
            self.assertIsNone(planner.junk_tier(name), name)

    def test_bundle_roots(self):
        cases = {
            ("proj", "venv", "Lib", "site-packages", "pkg"): 1,                     # project holding a venv
            ("proj", "venv", "lib", "python3.12", "site-packages", "x"): 1,         # posix layout
            ("Python312", "Lib", "site-packages"): 1,                               # system install
            ("proj", "node_modules", "left-pad"): 1,
            ("a", "b", ".git", "objects"): 2,
            ("proj", "src"): 0,                                                     # ordinary folders
            ("venv", "Lib", "site-packages"): 0,                                    # the scan target itself is never a unit
            (".git",): 0,
        }
        for parts, expected in cases.items():
            self.assertEqual(planner.bundle_root_parts(list(parts)), expected, parts)

    def test_split_files_is_a_partition(self):
        t = os.path.abspath("target")
        paths = [os.path.join(t, p) for p in (
            "proj/venv/Lib/site-packages/a.py", "proj/src/main.py", "proj/.git/HEAD",
            "docs/tax.txt", "docs/Thumbs.db", "old/backup.bak", "top.txt")]
        units, junk, routed = planner.split_files(t, paths)
        self.assertEqual(units, {os.path.join(t, "proj"): 3})
        self.assertEqual({os.path.basename(p): tier for p, tier in junk.items()}, {"Thumbs.db": "safe", "backup.bak": "review"})
        self.assertEqual(sorted(os.path.basename(p) for p in routed), ["tax.txt", "top.txt"])
        self.assertEqual(sum(units.values()) + len(junk) + len(routed), len(paths))

    def test_managed_folders_are_recognised(self):
        for name in ("10.01_Tax", "80.01_Unsorted_Miscellaneous", planner.SAFE_DIR, planner.REVIEW_DIR, planner.SOFTWARE_DIR):
            self.assertTrue(planner.is_managed_dir(name), name)
        for name in ("Taxes", "2023", "10.1_x", "_private"):
            self.assertFalse(planner.is_managed_dir(name), name)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="johnny_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "jd")
        self.inbox = os.path.join(self.root, "inbox")
        os.makedirs(self.inbox)
        self.index = fakes.make_index(os.path.join(self.root, "index.json"))
        for patcher in (
            mock.patch.object(utils, "UNDO_LOG", os.path.join(self.tmp, "undo.jsonl")),
            mock.patch.object(utils, "datetime", fakes.FixedClock),
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

    def run_organizer(self, *flags):
        argv = ["organizer.py", self.inbox, "--index", self.index, "--fast-seed", "--dest", self.root, *flags]
        with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()) as out:
            try:
                organizer.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()


class RouteSpecial(Base):
    def test_folder_moves_whole_keeps_origin_writes_note_and_never_overwrites(self):
        router = Router(IndexManager(self.index), self.root)
        self.put("work/proj/a.py")
        self.put("work/proj/sub/b.py")
        moved = router.route_special(os.path.join(self.inbox, "work", "proj"), planner.SOFTWARE_DIR, "work")
        self.assertEqual(moved, os.path.join(self.root, planner.SOFTWARE_DIR, "work", "proj"))
        self.assertTrue(os.path.isfile(os.path.join(moved, "sub", "b.py")))
        self.assertFalse(os.path.exists(os.path.join(self.inbox, "work", "proj")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, planner.SOFTWARE_DIR, "_README.txt")))
        self.put("work/proj/c.py")                                      # same folder name again: gets a suffix
        again = router.route_special(os.path.join(self.inbox, "work", "proj"), planner.SOFTWARE_DIR, "work")
        self.assertNotEqual(again, moved)
        self.assertTrue(os.path.isfile(os.path.join(moved, "a.py")))


class OrganizerWithRules(Base):
    def populate(self, tax_files=150):
        self.put("proj/venv/Lib/site-packages/pkg/mod.py", "m")
        self.put("proj/src/main.py", "s")
        self.put("proj/.git/HEAD", "ref")
        self.put("photos/Thumbs.db", "t")
        self.put("old/backup.bak", "b")
        self.put("scratch/x.tmp", "tmp")
        for i in range(tax_files):
            self.put(f"docs/tax_{i}.txt", str(i))

    def test_read_only_reports_but_moves_nothing(self):
        self.populate(5)
        before = tree(self.inbox)
        code, out = self.run_organizer("--read-only")
        self.assertEqual(code, 0)
        self.assertEqual(tree(self.inbox), before)
        self.assertIn("software_units=1", out)
        self.assertIn("junk_safe=2 junk_review=1", out)
        self.assertIn("Would move software folder as one unit (3 files): proj", out)

    def test_apply_moves_units_whole_junk_by_tier_and_undo_restores_everything(self):
        self.populate(150)
        before = tree(self.inbox)
        code, _ = self.run_organizer()
        self.assertEqual(code, 0)
        unit = os.path.join(self.root, planner.SOFTWARE_DIR, "proj")
        expected = sorted(os.path.join(*p.split("/")) for p in ("venv/Lib/site-packages/pkg/mod.py", "src/main.py", ".git/HEAD"))
        self.assertEqual(sorted(tree(unit)), expected)
        self.assertTrue(os.path.isfile(os.path.join(self.root, planner.SAFE_DIR, "photos", "Thumbs.db")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, planner.SAFE_DIR, "scratch", "x.tmp")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, planner.REVIEW_DIR, "old", "backup.bak")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "11.01_Tax", "tax_0.txt")))
        self.assertEqual(tree(self.inbox), {})
        utils.undo_renames(dry_run=False)
        self.assertEqual(tree(self.inbox), before)                      # byte-identical, every path restored

    def test_guard_ignores_files_handled_by_rules(self):
        for i in range(200):                                            # 200 junk files must not dilute or trip the guard
            self.put(f"junk/f{i}.tmp")
        for i in range(3):
            self.put(f"docs/tax_{i}.txt")
        self.assertEqual(self.run_organizer()[0], 0)

    def test_guard_still_blocks_when_the_router_fails_on_routed_files(self):
        for i in range(200):
            self.put(f"junk/f{i}.tmp")
        self.put("docs/tax_a.txt")
        self.put("docs/mystery.txt")                                    # 1 unsorted of 2 routed (allowed 0)
        before = tree(self.inbox)
        code, out = self.run_organizer()
        self.assertEqual(code, 2)
        self.assertIn("routed files", out)
        self.assertEqual(tree(self.inbox), before)


class FolderVote(unittest.TestCase):
    UNS = "Unsorted_Miscellaneous"

    def vote(self, folders, **kw):
        """folders: [(folder, [(final, nearest), ...])] -> (topics, moved)"""
        paths, topics, near = [], [], []
        for folder, files in folders:
            for i, (final, closest) in enumerate(files):
                paths.append(os.path.join(*folder.split("/"), f"f{i}.txt"))
                topics.append(final)
                near.append(closest)
        return planner.folder_vote(paths, topics, near, **kw)

    def test_refused_files_follow_a_folder_that_agrees(self):
        topics, moved = self.vote([("music", [("Music", "Music")] * 5 + [(self.UNS, "Music")] * 25)])
        self.assertEqual((moved, set(topics)), (25, {"Music"}))

    def test_a_mixed_folder_stays_unsorted(self):
        topics, moved = self.vote([("mix", [(self.UNS, "Music")] * 15 + [(self.UNS, "Books")] * 15)])
        self.assertEqual((moved, set(topics)), (0, {self.UNS}))

    def test_the_deepest_clean_folder_wins_inside_a_mixed_parent(self):
        files = [("lib/books", [(self.UNS, "Books")] * 25), ("lib/songs", [(self.UNS, "Music")] * 25)]
        topics, moved = self.vote(files)
        self.assertEqual((moved, topics[:25], topics[25:]), (50, ["Books"] * 25, ["Music"] * 25))

    def test_small_folders_and_unsorted_winners_do_not_vote(self):
        self.assertEqual(self.vote([("tiny", [(self.UNS, "Music")] * 10)])[1], 0)
        self.assertEqual(self.vote([("odd", [(self.UNS, self.UNS)] * 30)])[1], 0)

    def test_files_directly_in_the_scanned_folder_never_vote(self):
        topics, moved = planner.folder_vote(["a.txt"] * 30, [self.UNS] * 30, ["Music"] * 30)
        self.assertEqual((moved, set(topics)), (0, {self.UNS}))

    def test_type_check_keeps_a_pdf_out_of_a_folder_of_songs(self):
        paths = [os.path.join("old", f"song{i}.mp3") for i in range(25)] + [os.path.join("old", "Form 16 2019.pdf")]
        topics, near = [self.UNS] * 26, ["Music"] * 25 + ["Finance"]
        plain, moved_plain = planner.folder_vote(paths, topics, near)
        checked, moved_checked = planner.folder_vote(paths, topics, near, type_check=True)
        self.assertEqual((moved_plain, plain[-1]), (26, "Music"))          # the plain vote also moves the pdf
        self.assertEqual((moved_checked, checked[-1]), (25, self.UNS))      # with the type check no song is a pdf, so the pdf waits

    def test_the_type_check_lets_a_type_through_when_the_folder_has_it(self):
        paths = [os.path.join("course", f"lesson{i}.mp4") for i in range(20)] + [os.path.join("course", f"lesson{i}.srt") for i in range(10)]
        topics = ["Code"] * 15 + [self.UNS] * 5 + ["Code"] * 5 + [self.UNS] * 5
        moved = planner.folder_vote(paths, topics, ["Code"] * 30, type_check=True)[1]
        self.assertEqual(moved, 10)                                         # srt files are common among the agreeing files

    def test_held_files_never_follow_a_folder(self):
        paths = [os.path.join("music", f"s{i}.mp3") for i in range(25)]
        topics, moved = planner.folder_vote(paths, [self.UNS] * 25, ["Music"] * 25, hold=[True] * 5 + [False] * 20)
        self.assertEqual((moved, topics[:5], set(topics[5:])), (20, [self.UNS] * 5, {"Music"}))

    def test_files_with_no_informative_word_are_recognised(self):
        sep = os.sep
        for path in (f"Old Stuff{sep}scan7300.pdf", f"Downloads{sep}IMG_4213.jpg", f"Misc{sep}New Microsoft Word Document (9433).docx", f"New Folder (2){sep}0005752.pdf"):
            self.assertFalse(planner.has_information(path), path)
        for path in (f"Photos{sep}Diwali 2019{sep}IMG_0187.jpg", f"Downloads{sep}Form 16 2019.pdf", f"Misc{sep}Hotel Booking Goa.pdf"):
            self.assertTrue(planner.has_information(path), path)

    def test_sorted_files_are_never_changed(self):
        topics, _ = self.vote([("mix", [("Books", "Music")] * 3 + [(self.UNS, "Music")] * 27)])
        self.assertEqual(topics[:3], ["Books"] * 3)


if __name__ == "__main__":
    unittest.main()
