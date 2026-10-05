"""Tk UI end to end: drives PrototypeApp's real handlers on temp folders, headless-ish.

The window is created but withdrawn; dialogs are mocked and recorded; the NLP engine is a fake
(no model); UI scan output goes to a temp dir; the undo log is private.
"""
import gc
import hashlib
import io
import json
import os
import shutil
import tempfile
import time
import tkinter
import unittest
from contextlib import redirect_stdout
from unittest import mock

import fakes
from johnny.ui import jd_ui_prototype as ui
from johnny.rename import rename_engine
from johnny.core import utils
try:
    _probe = tkinter.Tk()
    _probe.destroy()
    TK_OK = True
except tkinter.TclError:
    TK_OK = False


def tree(folder):
    out = {}
    for root, _, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            with open(p, "rb") as f:
                out[os.path.relpath(p, folder)] = hashlib.sha256(f.read()).hexdigest()
    return out


@unittest.skipUnless(TK_OK, "no display available for Tk")
class UiFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="johnny_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "jd")
        self.inbox = os.path.join(self.root, "inbox")
        os.makedirs(self.inbox)
        self.index = fakes.make_index(os.path.join(self.root, "index.json"))
        self.boxes = mock.MagicMock()
        self.boxes.askyesno.return_value = True
        self.boxes.askyesnocancel.return_value = True
        for patcher in (
            mock.patch.object(ui, "messagebox", self.boxes),
            mock.patch.object(ui, "NLPEngine", fakes.FakeNLP),
            mock.patch.object(rename_engine, "NLPEngine", fakes.FakeNLP),
            mock.patch.object(ui, "PROJECT_DIR", ui.Path(self.tmp)),
            mock.patch.object(ui, "default_sorted_root", lambda: self.root),
            mock.patch.object(utils, "UNDO_LOG", os.path.join(self.tmp, "undo.jsonl")),
            mock.patch.object(utils, "datetime", fakes.FixedClock),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = ui.PrototypeApp()
        self.app.withdraw()
        self.addCleanup(self.close_app)
        self.app.target_path.set(self.inbox)
        self.app.index_path.set(self.index)

    def close_app(self):
        for job in self.app.tk.splitlist(self.app.tk.call("after", "info")):
            self.app.after_cancel(job)
        self.app.destroy()
        self.app = None
        gc.collect()  # free Tk variables on the main thread, not in a worker (Tcl_AsyncDelete)

    def put(self, rel, content="x"):
        p = os.path.join(self.inbox, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(content)

    def pump(self):
        """Run the real Tk mainloop (worker threads call Tk, which needs one) until the worker and queue are idle."""
        started = time.time()

        def check():
            idle = (self.app.worker is None or not self.app.worker.is_alive()) and self.app.results_queue.empty()
            if idle or time.time() - started > 15:
                self.app.after(300, self.app.quit)      # let the last poll/after callbacks land
            else:
                self.app.after(50, check)

        self.app.after(50, check)
        with redirect_stdout(io.StringIO()):            # the router prints from the worker thread
            self.app.mainloop()

    def errors(self):
        return [c.args[1] for c in self.boxes.showerror.call_args_list]

    def test_plan_profiles_are_carried_into_the_generated_index(self):
        plan = {"categories": [{"id": "10-19", "name": "Money", "subcategories": [{"id": "1", "name": "Bank Statements"}]}],
                "profiles": {"Bank Statements": {"description": "Bank statements.", "aliases": ["statement"], "positive_examples": ["x.pdf"]}, "Bad": "str"}}
        txt = os.path.join(self.tmp, "plan.txt")
        with open(txt, "w", encoding="utf-8") as f:
            json.dump(plan, f)
        index = ui.IndexManager(self.app._build_json_index_from_txt(txt))
        self.assertEqual(index.profiles["Bank_Statements"]["aliases"], ["statement"])
        self.assertNotIn("Bad", index.profiles)

    def test_merge_into_master_index_keeps_profiles(self):
        source = os.path.join(self.tmp, "source.json")
        with open(source, "w", encoding="utf-8") as f:
            json.dump({"categories": {"Tax": "11.01"}, "incubation": {}, "profiles": {"Tax": {"description": "Tax."}}}, f)
        cwd = os.getcwd()
        os.chdir(self.tmp)                                   # the merge writes ./master_index.json
        self.addCleanup(os.chdir, cwd)
        master = ui.IndexManager(self.app._merge_source_into_master_index(source))
        self.assertEqual(master.profiles["Tax"]["description"], "Tax.")

    def test_organize_plan_builds_the_engine_with_the_index_profiles(self):
        index = ui.IndexManager(self.index)
        index.data["profiles"] = {"Tax": {"description": "Tax filings."}}
        index._save()
        self.put("tax_a.txt")
        fakes.FakeNLP.instances.clear()
        self.organize(self.app._organize_preview)
        self.assertTrue(any(e.profiles and e.profiles.get("Tax") == {"description": "Tax filings."} for e in fakes.FakeNLP.instances))

    def test_organize_handles_software_units_and_junk_by_rules(self):
        self.app.include_subfolders.set(True)
        for rel in ("proj/venv/Lib/site-packages/a.py", "proj/src/main.py", "photos/Thumbs.db", "old/backup.bak", "scratch/x.tmp"):
            self.put(rel)
        for i in range(3):
            self.put(f"docs/tax_{i}.txt", str(i))
        before = tree(self.inbox)
        self.organize(self.app._organize_preview)
        self.assertEqual(tree(self.inbox), before, "preview moved files")
        rows = {r[0]: int(r[1]) for r in self.app.current_preview_rows}
        self.assertEqual(rows, {"_Software_Projects": 2, "_Temp_Safe_To_Remove": 2, "_Temp_Review_First": 1, "11.01_Tax": 3})
        self.assertEqual(sum(rows.values()), len(before))
        self.assertEqual(str(self.app.organize_primary_button.cget("state")), "normal")   # rules-handled files do not trip the guard
        self.organize(self.app._organize_apply)
        self.assertEqual(self.errors(), [])
        self.assertEqual(tree(self.inbox), {})
        self.assertTrue(os.path.isfile(os.path.join(self.root, "_Software_Projects", "proj", "src", "main.py")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "_Temp_Safe_To_Remove", "photos", "Thumbs.db")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "_Temp_Review_First", "old", "backup.bak")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "11.01_Tax", "tax_0.txt")))
        self.assertEqual(sum(int(r[1]) for r in self.app.current_preview_rows), 8)             # moved rows add up to every source file

    def test_rerun_does_not_plan_the_folders_this_tool_created(self):
        for name in ("11.01_Tax", "_Software_Projects", "_Temp_Safe_To_Remove", "_Temp_Review_First"):
            self.put(f"{name}/already.txt")
        self.put("new/tax_a.txt")
        found = [os.path.relpath(p, self.inbox) for p in ui.iter_files(self.inbox, True)]
        self.assertEqual(found, [os.path.join("new", "tax_a.txt")])

    def test_all_three_steps_render(self):
        for step in ("Scan", "Rename", "Organize", "Scan"):
            self.app._show_step(step)
            self.app.update()
            self.assertEqual(self.app.current_step.get(), step)

    def test_scan_writes_csv_without_touching_files(self):
        for name in ("a.txt", "sub/b.pdf", ".hidden/c.txt"):
            self.put(name)
        self.app.include_subfolders.set(True)
        before = tree(self.inbox)
        self.app._scan_create_log()
        self.pump()
        self.assertEqual(tree(self.inbox), before)
        csv_path = os.path.join(self.tmp, "runs", ui.safe_session_name(self.inbox), "drive_scan.csv")
        with open(csv_path, encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()) - 1, 2)      # header + 2 visible files
        self.assertEqual(self.app.summary_stats["Files"].get(), "2")
        self.assertEqual(self.errors(), [])

    def test_quick_rename_preview_matches_apply(self):
        self.put("Invoice 2023 Amazon copy.PDF", "A"); self.put("Invoice 2023 Amazon copy (1).PDF", "B"); self.put("keep_me.txt", "C")
        before = tree(self.inbox)
        self.app._show_step("Rename")
        self.app._rename_preview()
        self.pump()
        self.assertEqual(tree(self.inbox), before, "preview changed files")
        preview = sorted((r[0], r[1]) for r in self.app.current_preview_rows if r[1])
        self.assertEqual(len(preview), 2)
        self.app._rename_apply()
        self.pump()
        applied = sorted((r[0], r[1]) for r in self.app.current_preview_rows if r[1])
        self.assertEqual(applied, preview)
        self.assertEqual(sorted(tree(self.inbox).values()), sorted(before.values()))
        self.assertTrue(all(new in os.listdir(self.inbox) for _, new in applied))
        self.assertEqual(self.errors(), [])

    def organize(self, action):
        self.app._show_step("Organize")
        self.app.organize_mode.set("Apply Organization")
        action()
        self.pump()

    def test_organize_preview_then_apply_moves_every_file(self):
        for i in range(150):
            self.put(f"tax_{i}.txt", str(i))
        self.put("study_notes.txt", "S")
        before = tree(self.inbox)
        self.organize(self.app._organize_preview)
        self.assertEqual(tree(self.inbox), before, "preview moved files")
        self.assertEqual(sum(int(r[1]) for r in self.app.current_preview_rows), len(before))
        self.assertEqual(str(self.app.organize_primary_button.cget("state")), "normal")
        self.organize(self.app._organize_apply)
        self.assertEqual(self.errors(), [])
        moved = {k: v for k, v in tree(self.root).items() if not k.startswith(("index", "inbox"))}
        self.assertEqual(sorted(moved.values()), sorted(before.values()))
        self.assertEqual(tree(self.inbox), {})
        self.assertTrue(os.path.exists(os.path.join(self.root, "12.01_Study", "study_notes.txt")))
        self.assertEqual(sum(int(r[1]) for r in self.app.current_preview_rows), len(before))

    def test_failing_index_disables_apply_and_blocks_it(self):
        for i in range(9):
            self.put(f"tax_{i}.txt")
        self.put("mystery.txt")                       # 1 unsorted of 10, allowed 0
        before = tree(self.root)
        self.organize(self.app._organize_preview)
        self.assertEqual(str(self.app.organize_primary_button.cget("state")), "disabled")
        self.organize(self.app._organize_apply)
        self.assertEqual(tree(self.root), before, "blocked apply still moved files")
        self.assertTrue(any("1/150" in e for e in self.errors()), self.errors())

    def test_failed_check_writes_the_leftover_file_for_the_next_llm_round(self):
        for i in range(9):
            self.put(f"tax_{i}.txt")
        self.put("odd/mystery.txt")                   # 1 unsorted of 10, allowed 0
        self.app.include_subfolders.set(True)
        self.organize(self.app._organize_preview)
        path = os.path.join(self.tmp, "runs", ui.safe_session_name(self.inbox), "llm_leftovers_handoff.json")
        with open(path, encoding="utf-8") as f:
            handoff = json.load(f)
        self.assertEqual([(g["folder"], g["files"]) for g in handoff["leftover_groups"]], [("odd", 1)])
        self.assertIn("closest_buckets", handoff["leftover_groups"][0])
        self.assertEqual(set(handoff["current_index"]["categories"]), {"Tax", "Study", "Unsorted_Miscellaneous"})
        self.assertIn("1 of 10", handoff["instructions"]["goal"])
        self.assertEqual(self.app.last_output_path, path)

    def test_refused_files_follow_their_folder_in_preview_and_apply_alike(self):
        for i in range(25):
            self.put(f"docs/almost_{i}.txt")                 # refused by the model, nearest to Study
        for i in range(5):
            self.put(f"docs/study_{i}.txt")
        self.app.include_subfolders.set(True)
        self.organize(self.app._organize_preview)
        self.assertEqual(self.errors(), [])
        self.assertEqual(self.app.folder_vote_moved, 25)
        self.assertEqual([tuple(r[:2]) for r in self.app.current_preview_rows], [("12.01_Study", "30")])
        self.assertEqual(str(self.app.organize_primary_button.cget("state")), "normal")
        self.organize(self.app._organize_apply)
        self.assertEqual(self.errors(), [])
        self.assertEqual(len(os.listdir(os.path.join(self.root, "12.01_Study"))), 30)

    def test_sorting_settings_window_validates_applies_and_drives_the_preview(self):
        for i in range(25):
            self.put(f"docs/almost_{i}.txt")                 # refused by the model, nearest to Study
        for i in range(5):
            self.put(f"docs/study_{i}.txt")
        self.app.include_subfolders.set(True)
        self.app._show_step("Organize")
        self.assertTrue(self.app.sort_button.winfo_manager(), "the button is shown on the Organize step")
        self.app._show_step("Scan")
        self.assertFalse(self.app.sort_button.winfo_manager(), "and hidden elsewhere")
        self.app._show_step("Organize")
        self.app._open_sort_settings()
        win = [w for w in self.app.winfo_children() if w.winfo_class() == "Toplevel"][0]
        win.lead.set("0.9")                                  # outside 0.000-0.050: refused, not silently changed
        win.apply()
        self.assertIn("Lead must be between", win.message.get())
        self.assertEqual(self.app.sort_settings, ui.sorting.presets()["first"])
        win.lead.set("0.020"); win.vote.set(False); win.apply()
        self.assertEqual((self.app.sort_settings.lead, self.app.sort_settings.vote), (0.02, False))
        self.app.organize_mode.set("Apply Organization")
        self.organize(self.app._organize_preview)            # vote off: the 25 refused files stay for the next pass
        self.assertEqual(self.app.folder_vote_moved, 0)
        self.assertEqual(self.app.sort_stats["unsorted"], 25)
        self.app.sort_settings = ui.sorting.presets()["second"]
        self.organize(self.app._organize_preview)
        self.assertEqual((self.app.folder_vote_moved, self.app.sort_stats["unsorted"]), (25, 0))

    def test_passing_check_writes_no_leftover_file(self):
        for i in range(150):
            self.put(f"tax_{i}.txt")
        self.organize(self.app._organize_preview)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "runs")))

    def test_missing_index_shows_error_and_changes_nothing(self):
        self.put("tax_a.txt")
        before = tree(self.root)
        self.app.index_path.set(os.path.join(self.root, "typo.json"))
        self.organize(self.app._organize_apply)
        self.assertEqual(tree(self.root), before)
        self.assertTrue(any("Index not found" in e for e in self.errors()), self.errors())


    def test_apply_cancel_moves_nothing_and_choose_another_folder_is_used_and_announced(self):
        for i in range(3):
            self.put(f"tax_{i}.txt", str(i))
        before = tree(self.inbox)
        self.boxes.askyesnocancel.return_value = None                  # Cancel
        self.app.organize_mode.set("Apply Organization")
        self.app._organize_apply()
        self.assertEqual(tree(self.inbox), before)
        chosen = os.path.join(self.tmp, "picked")
        os.makedirs(chosen)
        self.boxes.askyesnocancel.side_effect = [False, True]          # No (choose another), then Yes
        with mock.patch.object(ui.filedialog, "askdirectory", return_value=chosen):
            self.organize(self.app._organize_apply)
        self.assertEqual(self.errors(), [])
        self.assertTrue(os.path.isfile(os.path.join(chosen, "11.01_Tax", "tax_0.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "11.01_Tax")))   # not the index's folder
        self.assertIn(chosen, self.boxes.showinfo.call_args.args[1])             # the notification names the folder
        self.assertEqual(self.app.last_output_path, chosen)


    def test_a_failed_move_is_not_counted_as_moved(self):
        """Audit T2: the after-apply sanity check must fail when a move fails, not pass on the untouched source."""
        for i in range(3):
            self.put(f"tax_{i}.txt", str(i))
        real = utils.safe_rename

        def flaky(src, dst, *args, **kwargs):
            if src.endswith("tax_1.txt"):
                raise PermissionError("locked")
            return real(src, dst, *args, **kwargs)

        with mock.patch("johnny.organize.router.safe_rename", flaky):
            self.organize(self.app._organize_apply)
        self.assertTrue(any("Sanity check failed after apply" in e and "moved files=2" in e for e in self.errors()), self.errors())
        self.assertTrue(os.path.exists(os.path.join(self.inbox, "tax_1.txt")))     # left where it was


if __name__ == "__main__":
    unittest.main()
