"""The synthetic drive and its answer key: a judge is only useful if the judge itself is sound (no model needed)."""
import csv
import os
import shutil
import tempfile
import unittest

import fakes  # noqa: F401  (puts the repo root on sys.path)
import make_synthetic_drive as gen
import synthetic_benchmark as sb
from johnny.core.planner import split_files


class SyntheticDrive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="johnny_test_")
        cls.rows = gen.write(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_it_is_deterministic_and_the_committed_files_are_current(self):
        for name in ("synthetic_scan.csv", "synthetic_truth.csv"):
            with open(os.path.join(self.tmp, name), "rb") as new, open(os.path.join(gen.OUT, name), "rb") as committed:
                self.assertEqual(new.read(), committed.read(), f"{name} differs: re-run tests/make_synthetic_drive.py and review the change")

    def test_every_scanned_file_has_one_answer_in_the_key(self):
        with open(os.path.join(self.tmp, "synthetic_scan.csv"), encoding="utf-8") as f:
            scan = [r["path"] for r in csv.DictReader(f)]
        self.assertEqual(len(scan), len(set(scan)))
        self.assertEqual(scan, [p for p, _, _ in self.rows])

    def test_answers_use_known_buckets_and_the_hard_situations_are_present(self):
        allowed = set(gen.BUCKETS) | {gen.UNS, "_Software_Projects", "_Temp_Safe_To_Remove"}
        self.assertTrue({b for _, b, _ in self.rows} <= allowed)
        tags = {t for _, _, t in self.rows}
        for hard in ("installer-package", "decoy-minority", "unknowable", "overlap", "deep-clean-children", "clean-generic", "transliterated", "dump-descriptive"):
            self.assertIn(hard, tags)
        self.assertTrue(all(b == gen.UNS for _, b, t in self.rows if t == "unknowable"))
        self.assertTrue(set(gen.BUCKETS) <= {b for _, b, _ in self.rows}, "every bucket should have files")

    def test_the_fixed_rules_take_out_exactly_the_files_the_key_says(self):
        paths = [p for p, _, _ in self.rows]
        units, junk, routed = split_files(".", paths)
        key_rules = {p for p, b, _ in self.rows if b in sb.RULE_BUCKETS}
        self.assertEqual(sum(units.values()) + len(junk), len(key_rules))
        self.assertTrue(key_rules.isdisjoint(routed))

    def test_scoring_counts_correct_wrong_and_missed(self):
        truth = {"a": ("Music", "clean"), "b": ("Music", "clean"), "c": ("Music", "clean"), "d": (gen.UNS, "unknowable"), "e": (gen.UNS, "unknowable")}
        bucket_map = {"Music_Library": "Music", "Photos": "Family_Photos"}
        assignments = [("a", "Music_Library", "model"), ("b", "Photos", "vote"), ("c", gen.UNS, "unsorted"), ("d", gen.UNS, "unsorted"), ("e", "Photos", "model")]
        all_ = sb.score(assignments, truth, bucket_map)["all"]
        self.assertEqual((all_["n"], all_["correct"], all_["wrong"], all_["missed"]), (5, 2, 2, 1))

    def test_a_rule_file_reaching_the_buckets_is_reported(self):
        truth = {"x": ("_Software_Projects", "rule-unit")}
        result = sb.score([("x", "Music_Library", "model")], truth, {})
        self.assertEqual((result["rule_failures"], "all" in result), (1, False))


if __name__ == "__main__":
    unittest.main()
