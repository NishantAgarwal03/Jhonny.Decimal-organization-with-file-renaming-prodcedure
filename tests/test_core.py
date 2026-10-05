"""Regression tests for the destructive paths (rename, move, undo, index). Stdlib only.

Run from the repo root:  python -m unittest discover -s tests -v
Every test works in a throwaway temp folder and a private undo log.
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fakes
from johnny.rename import filename_standardizer
from johnny.organize import router
from johnny.core import utils
from johnny.rename.filename_standardizer import FilenameStandardizer
from johnny.core.embedding_manager import missing_profiles
from johnny.core.index_manager import DEFAULT_PROFILES, IndexManager
from johnny.core import evidence_engine
from johnny.core import nlp_engine
from johnny.rename.rename_engine import ContextualRenamer
from johnny.organize.router import Router


def read(path):
    with open(path) as f:
        return f.read()


def snapshot(folder):
    out = {}
    for root, _, files in os.walk(folder):
        for name in files:
            with open(os.path.join(root, name), "rb") as f:
                out[os.path.relpath(os.path.join(root, name), folder)] = hashlib.sha256(f.read()).hexdigest()
    return out


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="johnny_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        self.log = os.path.join(self.tmp, "undo.jsonl")
        for patcher in (mock.patch.object(utils, "UNDO_LOG", self.log), mock.patch.object(utils, "datetime", fakes.FixedClock)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, rel, content="x"):
        path = os.path.join(self.work, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return path


class SafeRenameAndUndo(TempCase):
    def test_refuses_overwrite(self):
        a, b = self.write("a.txt", "A"), self.write("b.txt", "B")
        with self.assertRaises(FileExistsError):
            utils.safe_rename(a, b)
        self.assertEqual(read(b), "B")
        self.assertFalse(os.path.exists(self.log))

    def test_undo_round_trip_restores_names_and_bytes(self):
        a = self.write("a.txt", "A")
        before = snapshot(self.work)
        utils.safe_rename(a, os.path.join(self.work, "b.txt"))
        utils.safe_rename(os.path.join(self.work, "b.txt"), os.path.join(self.work, "c.txt"))
        self.assertEqual({r["status"] for r in utils.undo_renames(dry_run=True)}, {"would_restore", "skipped_missing"})
        utils.undo_renames(dry_run=False)
        self.assertEqual(snapshot(self.work), before)
        self.assertEqual(read(self.log), "")

    def test_undo_never_overwrites_occupied_original(self):
        a = self.write("a.txt", "A")
        utils.safe_rename(a, os.path.join(self.work, "b.txt"))
        self.write("a.txt", "NEW")
        self.assertEqual(utils.undo_renames(dry_run=False)[0]["status"], "skipped_occupied")
        self.assertEqual(read(a), "NEW")


class RouterMoves(TempCase):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "jd")
        index = IndexManager(os.path.join(self.tmp, "index.json"), create=True)
        index.data["categories"] = {"Tax": "11.01", "Unsorted_Miscellaneous": "80.01"}
        index._save()
        self.router = Router(index, self.root)

    def test_move_returns_existing_destination(self):
        src = self.write("a.txt")
        dest = self.router.route_file(src, "Tax")
        self.assertTrue(os.path.exists(dest))
        self.assertFalse(os.path.exists(src))

    def test_collision_never_overwrites(self):
        first = self.router.route_file(self.write("a.txt", "1"), "Tax")
        second = self.router.route_file(self.write("a.txt", "2"), "Tax")
        self.assertNotEqual(first, second)
        self.assertEqual(read(first), "1")

    def test_rename_and_move_collide_the_same_way(self):
        folder = os.path.join(self.root, "11.01_Tax")
        os.makedirs(folder)
        open(os.path.join(folder, "a.txt"), "w").close()
        moved = self.router.route_file(self.write("a.txt", "y"), "Tax")
        renamed = utils.generate_safe_filename(folder, "a", ".txt")
        self.assertEqual(os.path.basename(moved), "a_1.txt")
        self.assertEqual(os.path.basename(renamed), "a_2.txt")

    def test_failed_move_returns_none_and_keeps_source(self):
        src = self.write("a.txt")
        with mock.patch.object(router, "safe_rename", side_effect=PermissionError("locked")):
            self.assertIsNone(self.router.route_file(src, "Tax"))
        self.assertTrue(os.path.exists(src))


class RenameParity(TempCase):
    NAMES = ["Invoice 2023 Amazon copy.PDF", "Invoice 2023 Amazon copy (1).PDF", "Invoice-2023-Amazon-copy.PDF", "sub/Invoice 2023 Amazon copy.PDF"]

    def planned(self, results):
        return sorted((os.path.relpath(r["path"], self.work), r["new_name"]) for r in results if r["status"] != "skipped")

    def check(self, engine, **kwargs):
        for name in self.NAMES:
            self.write(name, name)
        before = snapshot(self.work)
        dry = engine.rename_target(self.work, dry_run=True, **kwargs)
        self.assertEqual(snapshot(self.work), before, "dry run changed files")
        planned = self.planned(dry)
        self.assertEqual(len({(os.path.dirname(p), n) for p, n in planned}), len(planned), "dry run planned duplicate targets")
        self.assertEqual(self.planned(engine.rename_target(self.work, dry_run=False, **kwargs)), planned)
        self.assertEqual(sorted(snapshot(self.work).values()), sorted(before.values()), "content changed")

    def test_quick_dry_run_matches_apply(self):
        self.check(FilenameStandardizer())

    def test_deep_dry_run_matches_apply(self):
        self.check(ContextualRenamer(), force=True)


class QuickRenameRules(unittest.TestCase):
    def setUp(self):
        self.s = FilenameStandardizer()

    def test_year_range_is_not_truncated(self):
        self.assertIsNone(self.s.standardize_name("ITR-V Verification Form PDF 2021-22.pdf"))
        self.assertEqual(self.s.standardize_name("Form 16 2021-22.pdf"), "FORM16_2021_22")

    def test_more_than_five_parts_skips_instead_of_truncating(self):
        self.assertIsNone(self.s.standardize_name("Acme_Bank Statement_and_Salary Slip_15-01-2023.png"))


class QuickRenameKeepsPersonNames(unittest.TestCase):
    """A person hint present in the original name must survive in the new name, or the file is skipped."""

    def setUp(self):
        self.s = FilenameStandardizer()
        patcher = mock.patch.dict(filename_standardizer.PERSON_HINTS, {"testperson": "TestPersonSurname"})  # synthetic: no real names in tests
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_person_is_kept_for_financial_documents(self):
        self.assertEqual(self.s.standardize_name("Testperson-Electricity Bill_15-01-2023.Draft.pdf"), "TestPersonSurname_ElectricityBill_2023-01-15_Draft")
        self.assertEqual(self.s.standardize_name("Testperson_Water Bill_final.pdf.bak"), "TestPersonSurname_WaterBill_Final")

    def test_person_in_identity_documents_still_kept(self):
        self.assertEqual(self.s.standardize_name("Testperson Aadhaar Card.pdf"), "TestPersonSurname_AadhaarCard_UIDAI")

    def test_gate_rejects_any_candidate_that_drops_a_person(self):
        words = ["Testperson", "Electricity", "Bill", "2023"]
        self.assertFalse(self.s.candidate_preserves_meaning("Testperson Electricity Bill 2023", "ElectricityBill_2023", words, "ElectricityBill", "financial_tax"))
        self.assertTrue(self.s.candidate_preserves_meaning("Testperson Electricity Bill 2023", "TestPersonSurname_ElectricityBill_2023", words, "ElectricityBill", "financial_tax"))

    def test_person_with_no_safe_place_in_the_name_is_skipped(self):
        self.assertIsNone(self.s.standardize_name("Testperson_Notes_Project_Phase_Final_Approved_By_Manager_Jan 2023.pdf"))


class QuickRenameDates(unittest.TestCase):
    """extract_date must be lossless: a real date is kept whole, an invalid or ambiguous one is not invented."""

    CASES = {
        "Bank Statement 2023-01-15": "2023-01-15",
        "%20Someone%20Bank Statement%202023-01-15": "2023-01-15",     # %20 is decoded before parsing
        "Electricity Bill_15-01-2023": "2023-01-15",               # dd-mm-yyyy keeps day and month
        "Invoice Amazon 31-12-2022": "2022-12-31",
        "Form 16 2021-22": "2021-22",                              # fiscal/academic range, not month 22
        "FY 2019-20": "2019-20",
        "Report 2022_05": "2022-05",
        "Notes Jan 2023": "2023-01",
        "Notes January_2023": "2023-01",
        "Aadhaar%2001152023": None,                                # month 15: ambiguous, so not a date
        "991399": None,
        "Jan-Mar 2023": "2023",                                    # a month range is not a single month
        "Statement March to June 2023": "2023",
        "Market 2022": "2022",
    }

    def test_extract_date_table(self):
        s = FilenameStandardizer()
        for text, expected in self.CASES.items():
            self.assertEqual(s.extract_date(text), expected, text)

    def test_dates_survive_in_renamed_files_without_duplicating_the_year(self):
        s = FilenameStandardizer()
        self.assertEqual(s.standardize_name("Bank Statement 2023-01-15.pdf"), "BankStatement_2023-01-15")
        self.assertEqual(s.standardize_name("Invoice Amazon 31-12-2022.pdf"), "Invoice_2022-12-31")
        with mock.patch.dict(filename_standardizer.PERSON_HINTS, {"testperson": "TestPersonSurname"}):
            self.assertEqual(s.standardize_name("%20Testperson%20Bank Statement%202023-01-15.pdf"), "TestPersonSurname_BankStatement_2023-01-15")


class ProfileCoverage(unittest.TestCase):
    def test_missing_profiles_lists_categories_without_an_entry(self):
        self.assertEqual(missing_profiles(["A", "B", "C"], {"B": {}}), ["A", "C"])
        self.assertEqual(missing_profiles([], {"B": {}}), [])
        self.assertEqual(missing_profiles(["B"], {"B": {}}), [])

    def test_shipped_profiles_do_not_cover_the_shipped_indexes(self):
        """Documents the real cause of high abstention: category_profiles.json is from another taxonomy."""
        import json
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not os.path.exists(os.path.join(root, "category_profiles.json")):
            self.skipTest("category_profiles.json is not shipped in the public release")
        with open(os.path.join(root, "category_profiles.json"), encoding="utf-8") as f:
            profiles = json.load(f)
        with open(os.path.join(root, "fixtures", "jd_benchmark_profiles.json"), encoding="utf-8") as f:
            fixture_profiles = json.load(f)
        with open(os.path.join(root, "fixtures", "jd_benchmark_index.json"), encoding="utf-8") as f:
            fixture_categories = list(json.load(f)["categories"])
        self.assertGreater(len(missing_profiles(fixture_categories, profiles)), 0)
        self.assertEqual(missing_profiles(fixture_categories, fixture_profiles), [])


PROFILE = {"description": "Bank statements and bills.", "aliases": ["bank statement"], "positive_examples": ["water bill.pdf"]}


class IndexProfiles(TempCase):
    def make(self, data):
        path = os.path.join(self.tmp, "index.json")
        index = IndexManager(path, create=True)
        index.data.update(data)
        index._save()
        return IndexManager(path)

    def test_profiles_come_from_the_index_and_default_bucket_is_always_present(self):
        index = self.make({"categories": {"Tax": "11.01", "Unsorted_Miscellaneous": "80.01"}, "profiles": {"Tax": PROFILE}})
        self.assertEqual(index.profiles["Tax"], PROFILE)
        self.assertEqual(index.profiles["Unsorted_Miscellaneous"], DEFAULT_PROFILES["Unsorted_Miscellaneous"])

    def test_malformed_and_unknown_profiles_are_ignored(self):
        index = self.make({"categories": {"Tax": "11.01"}, "profiles": {"Tax": "just a string", "Ghost": PROFILE}})
        self.assertNotIn("Tax", index.profiles)
        self.assertNotIn("Ghost", index.profiles)

    def test_index_without_profiles_key_still_works(self):
        self.assertEqual(set(self.make({"categories": {"Tax": "11.01"}}).profiles), {"Unsorted_Miscellaneous"})

    def test_engine_no_longer_falls_back_to_the_unrelated_shipped_profile_file(self):
        with mock.patch.object(nlp_engine.EmbeddingManager, "is_available", return_value=False):
            self.assertEqual(set(nlp_engine.NLPEngine(["Tax"]).category_profiles), {"Unsorted_Miscellaneous"})
            self.assertEqual(nlp_engine.NLPEngine(["Tax"], category_profiles={}).category_profiles, {})

    def test_llm_handoff_asks_for_profiles(self):
        path = os.path.join(self.tmp, "handoff.json")
        evidence_engine.write_llm_handoff_json(path, "s", [], [], {}, 100)
        import json
        with open(path, encoding="utf-8") as f:
            handoff = json.load(f)
        self.assertTrue(any("profiles" in r for r in handoff["instructions"]["requirements"]))
        self.assertIn("profiles", handoff["instructions"]["output_contract"]["optional_keys"])

    ROWS = [{"path": f"Study/Soil/Lec {i}.mp4", "name": f"Lec {i}", "type": ".mp4"} for i in range(30)] + [
        {"path": "Music/a.mp3", "name": "a", "type": ".mp3"}, {"path": "venv/x.py", "name": "x", "type": ".py"},
        {"path": "proj/.venv/Lib/site-packages/m.py", "name": "m", "type": ".py"}, {"path": "t.tmp", "name": "t", "type": ".tmp"}]

    def test_handoff_gives_the_llm_a_folder_map_and_the_author_note(self):
        path = os.path.join(self.tmp, "handoff.json")
        evidence_engine.write_llm_handoff_json(path, "s", [], [], {}, 100, scan_rows=self.ROWS, author_note="Civil study drive")
        import json
        with open(path, encoding="utf-8") as f:
            handoff = json.load(f)
        self.assertEqual(handoff["author_note"], "Civil study drive")
        overview = handoff["drive_overview"]
        self.assertEqual((overview["files_total"], overview["junk_files_handled_by_rule"], overview["software_units_handled_by_rule"]["files"]), (34, 1, 1))
        self.assertEqual(overview["files_needing_a_bucket"], 32)
        top = handoff["folder_map"][0]
        self.assertEqual((top["folder"], top["files"], top["top_types"], len(top["sample_names"])), ("Study" + chr(92) + "Soil", 30, {".mp4": 30}, 5))
        self.assertEqual(handoff["file_type_table"][0]["type"], ".mp4")
        self.assertIn("notes", handoff["instructions"]["output_contract"]["optional_keys"])

    def test_leftovers_are_grouped_by_folder_with_the_closest_buckets(self):
        paths = [f"Pics/a{i}.jpg" for i in range(7)] + ["Misc/b.dat"]
        groups = evidence_engine.group_leftovers(paths, nearest=lambda samples: [{"category": "Photos", "n": len(samples)}])
        self.assertEqual([(g["folder"], g["files"]) for g in groups], [("Pics", 7), ("Misc", 1)])
        self.assertEqual(groups[0]["closest_buckets"], [{"category": "Photos", "n": 5}])
        self.assertEqual(groups[1]["sample_names"], ["b.dat"])


class BatchSorting(unittest.TestCase):
    """get_topic_details_many must decide exactly like get_topic_details, and reuse cached file embeddings."""

    def make_engine(self):
        import numpy as np

        class Stub:
            calls = 0

            def encode_queries(self, texts, model_name=None):
                Stub.calls += len(texts)
                out = []
                for t in texts:
                    v = np.random.default_rng(int(hashlib.sha1(t.encode()).hexdigest()[:8], 16)).normal(size=8)
                    out.append(v / np.linalg.norm(v))
                return np.array(out)

        eng = object.__new__(nlp_engine.NLPEngine)
        eng.categories = ["A", "B", "Unsorted_Miscellaneous"]
        eng.model_name, eng.model, eng.embedding_manager = "stub", object(), Stub()
        eng.prototype_lookup = ["A", "A", "B", "Unsorted_Miscellaneous"]
        proto = np.random.default_rng(1).normal(size=(4, 8))
        eng.category_embeddings = proto / np.linalg.norm(proto, axis=1, keepdims=True)
        eng.routing_calibration = {"dense_confidence_threshold": 0.1, "top1_top2_margin_threshold": 0.05, "unsorted_override_threshold": 0.05}
        return eng, Stub

    def test_batch_decisions_match_single_decisions(self):
        eng, _ = self.make_engine()
        texts = [f"file {i} words" for i in range(60)] + ["", "   "]
        single = [eng.get_topic_details(t)[:2] for t in texts]
        many = eng.get_topic_details_many(texts)
        self.assertEqual([c for c, _ in single], [c for c, _, _ in many])
        for (_, s1), (_, s2, _) in zip(single, many):
            self.assertAlmostEqual(s1, s2, places=6)
        self.assertEqual(many[-1][0], "Unsorted_Miscellaneous")
        self.assertEqual(len({c for c, _, _ in many}), 3, "the stub should exercise every outcome")

    def test_cache_means_a_second_round_encodes_nothing(self):
        eng, Stub = self.make_engine()
        cache = os.path.join(tempfile.mkdtemp(prefix="johnny_test_"), "cache.npz")
        texts = [f"file {i}" for i in range(30)]
        first = eng.get_topic_details_many(texts, cache_path=cache)
        self.assertEqual(Stub.calls, 30)
        again = eng.get_topic_details_many(texts + ["one new file"], cache_path=cache)
        self.assertEqual(Stub.calls, 31)
        self.assertEqual([r[0] for r in first], [r[0] for r in again[:30]])


class ModelPrefixes(unittest.TestCase):
    """E5 models need "query: " / "passage: "; other models must get the plain text."""

    def test_only_e5_models_get_prefixes(self):
        from johnny.core.embedding_manager import EmbeddingManager

        class Spy:
            seen = None

            def encode(self, texts, normalize_embeddings=True):
                Spy.seen = list(texts)
                return texts

        manager = EmbeddingManager()
        for name in ("intfloat/multilingual-e5-small", "Alibaba-NLP/gte-multilingual-base"):
            manager._models[name] = Spy()
        manager.encode_queries(["a"], model_name="intfloat/multilingual-e5-small")
        self.assertEqual(Spy.seen, ["query: a"])
        manager.encode_passages(["b"], model_name="intfloat/multilingual-e5-small")
        self.assertEqual(Spy.seen, ["passage: b"])
        manager.encode_queries(["a"], model_name="Alibaba-NLP/gte-multilingual-base")
        self.assertEqual(Spy.seen, ["a"])


class IndexManagerRules(TempCase):
    def test_missing_index_raises_and_creates_nothing(self):
        path = os.path.join(self.tmp, "typo.json")
        with self.assertRaises(FileNotFoundError):
            IndexManager(path)
        self.assertFalse(os.path.exists(path))

    def test_create_and_atomic_save(self):
        path = os.path.join(self.tmp, "new.json")
        index = IndexManager(path, create=True)
        index.mint_category("A")
        self.assertEqual(IndexManager(path).data["categories"], {"A": "10.01"})
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_mint_category_uses_the_true_highest_code_and_rejects_malformed_ones(self):
        index = IndexManager(os.path.join(self.tmp, "mint.json"), create=True)
        index.data["categories"] = {"B": "12.05", "A": "10.01"}                 # highest code is not the last one
        self.assertEqual(index.mint_category("C"), "12.06")
        for bad in ("abc", "1.x", "1.5", "12.5.1"):
            index.data["categories"]["Bad"] = bad
            with self.assertRaises(ValueError):
                index.mint_category("D")


if __name__ == "__main__":
    unittest.main()
