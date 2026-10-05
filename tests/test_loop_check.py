"""loop_check: the bucket loop's check run from a scan CSV alone (fake NLP, temp folders only)."""
import csv
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import fakes
from johnny.tools import loop_check


class LoopCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="johnny_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.index = fakes.make_index(os.path.join(self.tmp, "index.json"))
        self.out = os.path.join(self.tmp, "out")
        patcher = mock.patch.object(loop_check, "NLPEngine", fakes.FakeNLP)
        patcher.start()
        self.addCleanup(patcher.stop)

    def scan(self, paths):
        path = os.path.join(self.tmp, "drive_scan.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["path", "name", "type"])
            for p in paths:
                name, ext = os.path.splitext(os.path.basename(p))
                writer.writerow([p, name, ext])
        return path

    def test_failing_check_writes_the_leftover_file_and_moves_nothing(self):
        paths = [f"tax/tax_{i}.txt" for i in range(9)] + ["odd/mystery.txt", "x/.git/config", "t.tmp"]
        scan = self.scan(paths)
        before = sorted(os.listdir(self.tmp))
        result = loop_check.check(scan, self.index, self.out)
        self.assertEqual((result["needing"], result["unsorted"], result["allowed"], result["passed"]), (10, 1, 0, False))
        self.assertEqual((result["units"], result["junk"]), (1, 1))
        with open(result["handoff"], encoding="utf-8") as f:
            handoff = json.load(f)
        self.assertEqual([(g["folder"], g["files"]) for g in handoff["leftover_groups"]], [("odd", 1)])
        self.assertIn("closest_buckets", handoff["leftover_groups"][0])
        self.assertEqual(sorted(set(os.listdir(self.tmp)) - {"out"}), before)

    def test_passing_check_writes_no_leftover_file(self):
        result = loop_check.check(self.scan([f"tax/tax_{i}.txt" for i in range(150)]), self.index, self.out)
        self.assertTrue(result["passed"])
        self.assertIsNone(result["handoff"])
        self.assertFalse(os.path.exists(os.path.join(self.out, "llm_leftovers_handoff.json")))

    def test_a_clean_folder_pulls_its_refused_files_in_and_a_mixed_one_does_not(self):
        paths = [f"docs/almost_{i}.txt" for i in range(25)] + [f"docs/study_{i}.txt" for i in range(5)]    # all nearest to Study
        paths += [f"mix/almost_{i}.txt" for i in range(15)] + [f"mix/tax_{i}.txt" for i in range(15)]      # half Study, half Tax
        result = loop_check.check(self.scan(paths), self.index, self.out)
        self.assertEqual((result["voted"], result["unsorted"], result["needing"]), (25, 15, 60))
        self.assertEqual(result["counts"]["Study"], 30)

    def test_a_scan_that_stores_its_root_in_every_path_cannot_vote_above_that_root(self):
        # every path starts with Users/Admin/Downloads, and most files point at Study: that is not a folder-level signal
        paths = [f"Users/Admin/Downloads/almost_{i}.txt" for i in range(25)] + [f"Users/Admin/Downloads/study_{i}.txt" for i in range(75)]
        result = loop_check.check(self.scan(paths), self.index, self.out)
        self.assertEqual((result["voted"], result["unsorted"]), (0, 25))
        self.assertEqual(loop_check.strip_scan_root(["a/b/c.txt", "a/b/d/e.txt"]), [os.path.join("c.txt"), os.path.join("d", "e.txt")])

    def test_round1_handoff_has_the_folder_map_and_note(self):
        note = os.path.join(self.tmp, "note.txt")
        with open(note, "w") as f:
            f.write("My study drive")
        out = loop_check.write_round1(self.scan(["a/b/c.pdf", "a/b/d.pdf", "e.txt"]), self.out, note)
        with open(out, encoding="utf-8") as f:
            handoff = json.load(f)
        self.assertEqual(handoff["author_note"], "My study drive")
        self.assertEqual(handoff["drive_overview"]["files_needing_a_bucket"], 3)
        self.assertEqual(handoff["folder_map"][0]["files"], 2)


if __name__ == "__main__":
    unittest.main()
