"""johnny/core/sorting.py: the shared decision (settings, presets, safeguards, folder vote), with the fake model."""
import json
import os
import shutil
import tempfile
import unittest

import fakes
from johnny.core import sorting


class SortingSettings(unittest.TestCase):
    def test_numbers_are_pulled_inside_their_ranges(self):
        s = sorting.clamp(sorting.SortSettings(lead=0.5, vote_share=0.1, min_files=1))
        self.assertEqual((s.lead, s.vote_share, s.min_files), (0.05, 0.5, 5))
        s = sorting.clamp(sorting.SortSettings(lead=-1, vote_share=2, min_files=999))
        self.assertEqual((s.lead, s.vote_share, s.min_files), (0.0, 1.0, 200))

    def test_presets_second_pass_follows_the_settings_file_and_first_pass_is_stricter(self):
        p = sorting.presets()
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "routing_calibration.json"), encoding="utf-8") as f:
            self.assertEqual(p["second"].lead, json.load(f)["top1_top2_margin_threshold"])        # one source of truth
        self.assertGreater(p["first"].lead, p["second"].lead)
        self.assertGreater(p["first"].vote_share, p["second"].vote_share)
        self.assertTrue(p["first"].no_info_rule and p["first"].vote_type_check and p["second"].no_info_rule and p["second"].vote_type_check)

    def test_a_passes_entry_in_the_settings_file_overrides_the_first_pass(self):
        tmp = tempfile.mkdtemp(prefix="johnny_test_")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "cal.json")
        with open(path, "w") as f:
            json.dump({"top1_top2_margin_threshold": 0.02, "passes": {"first": {"lead": 0.04, "min_files": 30}}}, f)
        p = sorting.presets(path)
        self.assertEqual((p["second"].lead, p["first"].lead, p["first"].min_files, p["first"].vote_share), (0.02, 0.04, 30, 0.9))


class SortFiles(unittest.TestCase):
    def run_sort(self, rels, texts=None, **settings):
        nlp = fakes.FakeNLP(["Study", "Tax", "Unsorted_Miscellaneous"])
        return sorting.sort_files(nlp, rels, texts or [os.path.basename(r) for r in rels], sorting.SortSettings(**settings))

    def test_files_with_no_informative_word_are_held_and_never_follow_their_folder(self):
        rels = [os.path.join("docs", f"almost_{i}.txt") for i in range(25)] + [os.path.join("docs", f"scan{i}.pdf") for i in range(5)]
        out = self.run_sort(rels)
        self.assertEqual(out["how"][:25], ["vote"] * 25)
        self.assertEqual(out["how"][25:], ["held"] * 5)
        self.assertEqual(out["stats"]["held_no_information"], 5)
        self.assertEqual(out["stats"]["unsorted"], 5)

    def test_turning_the_vote_off_leaves_refused_files_for_the_next_pass(self):
        rels = [os.path.join("docs", f"almost_{i}.txt") for i in range(25)] + [os.path.join("docs", f"study_{i}.txt") for i in range(5)]
        on, off = self.run_sort(rels), self.run_sort(rels, vote=False)
        self.assertEqual((on["stats"]["voted"], on["stats"]["unsorted"]), (25, 0))
        self.assertEqual((off["stats"]["voted"], off["stats"]["unsorted"]), (0, 25))

    def test_the_safeguards_can_be_switched_off_in_code_only(self):
        rels = [os.path.join("old", f"scan{i}.pdf") for i in range(3)]
        self.assertEqual(self.run_sort(rels)["how"], ["held"] * 3)
        self.assertNotIn("held", self.run_sort(rels, no_info_rule=False)["how"])

    def test_out_of_range_settings_are_clamped_before_use(self):
        nlp = fakes.FakeNLP(["Study"])
        nlp.routing_calibration = {"top1_top2_margin_threshold": 0.01}
        sorting.sort_files(nlp, ["a.txt"], ["a"], sorting.SortSettings(lead=9))
        self.assertEqual(nlp.routing_calibration["top1_top2_margin_threshold"], 0.05)


if __name__ == "__main__":
    unittest.main()
