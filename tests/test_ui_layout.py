"""Tk layout contract. Geometry checks via winfo (DPI-independent), window kept off-screen but mapped.

1. Hard contracts: one panel per step, primary actions mapped and sized, minsize honoured,
   no two interactive widgets overlapping.
2. Clipping ratchet: KNOWN_CLIPPING lists layout defects that exist today (none today; the layout uses pack/grid).
   The test fails on any NEW clipped / truncated widget, so the list can only shrink.
   Print the current set with:  python tests/test_ui_layout.py --print
"""
import sys
import unittest

import fakes  # noqa: F401  (puts the repo root on sys.path)
from johnny.ui import jd_ui_prototype as ui
try:
    import tkinter
    _probe = tkinter.Tk()
    _probe.destroy()
    TK_OK = True
except Exception:
    TK_OK = False

SIZES = [(1100, 700), (1240, 800), (1366, 768)]
VIEWS = [("Scan", None), ("Rename", "Quick"), ("Rename", "Deep"), ("Organize", "Category Plan"), ("Organize", "Apply Organization")]
INTERACTIVE = {"Button", "Radiobutton", "Checkbutton", "Entry", "Treeview"}
TEXT_CLASSES = {"Button", "Label", "Radiobutton", "Checkbutton"}

# (size, step, mode, kind, widget class, widget text) -> known defect. Empty: keep it that way.
KNOWN_CLIPPING = set()


def walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


def text_of(widget):
    try:
        return " ".join(str(widget.cget("text")).split())[:40].encode("ascii", "replace").decode()
    except Exception:
        return ""


def collect_issues(app, size, step, mode):
    issues = set()
    for w in walk(app):
        if not w.winfo_ismapped():
            continue
        cls, parent = w.winfo_class(), w.nametowidget(w.winfo_parent())
        x, y, wd, ht = w.winfo_x(), w.winfo_y(), w.winfo_width(), w.winfo_height()
        if x < 0 or y < 0 or x + wd > parent.winfo_width() + 1 or y + ht > parent.winfo_height() + 1:
            issues.add((size, step, mode, "clipped", cls, text_of(w)))
        if cls in TEXT_CLASSES:
            if wd > 1 and w.winfo_reqwidth() > wd + 1:
                issues.add((size, step, mode, "text-truncated-w", cls, text_of(w)))
            if ht > 1 and w.winfo_reqheight() > ht + 1:
                issues.add((size, step, mode, "text-truncated-h", cls, text_of(w)))
    return issues


@unittest.skipUnless(TK_OK, "no display available for Tk")
class UiLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ui.PrototypeApp()
        cls.app.geometry("920x620+20000+20000")   # mapped but off-screen
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        for job in cls.app.tk.splitlist(cls.app.tk.call("after", "info")):
            cls.app.after_cancel(job)
        cls.app.destroy()

    def show(self, size, step, mode):
        app = self.app
        app.geometry(f"{size[0]}x{size[1]}+20000+20000")
        app._show_step(step)
        if step == "Rename":
            app.rename_mode.set(mode); app._refresh_rename_copy()
        elif step == "Organize":
            app.organize_mode.set(mode); app._refresh_organize_copy()
        app.update_idletasks(); app.update()

    def test_one_panel_per_step(self):
        panels = {"Scan": self.app.scan_panel, "Rename": self.app.rename_panel, "Organize": self.app.organize_panel}
        for step in panels:
            self.show(SIZES[1], step, "Quick" if step == "Rename" else "Category Plan")
            self.assertEqual([n for n, p in panels.items() if p.winfo_ismapped()], [step])
            self.assertEqual(self.app.step_buttons[step].cget("bg"), "#eef5f1")

    def test_core_widgets_are_mapped_and_sized_in_every_view(self):
        for size in SIZES:
            for step, mode in VIEWS:
                self.show(size, step, mode)
                for name in ("sidebar", "main", "summary_bar", "preview_table"):
                    w = getattr(self.app, name)
                    self.assertTrue(w.winfo_ismapped() and w.winfo_width() > 50 and w.winfo_height() > 50, (size, step, mode, name))
                for btn in self.app.step_buttons.values():
                    self.assertTrue(btn.winfo_ismapped() and btn.winfo_width() > 50, (size, step, mode))

    def test_window_honours_minimum_size(self):
        self.app.geometry("300x200+20000+20000")
        self.app.update()
        self.assertGreaterEqual((self.app.winfo_width(), self.app.winfo_height()), (1100, 700))

    def test_interactive_widgets_do_not_overlap(self):
        for size in SIZES:
            for step, mode in VIEWS:
                self.show(size, step, mode)
                boxes = [(w, w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height())
                         for w in walk(self.app) if w.winfo_ismapped() and w.winfo_class() in INTERACTIVE and w.winfo_width() > 1]
                for i, (a, ax, ay, aw, ah) in enumerate(boxes):
                    for b, bx, by, bw, bh in boxes[i + 1:]:
                        if "treeview" in (a.winfo_class().lower(), b.winfo_class().lower()):
                            continue
                        overlap = min(ax + aw, bx + bw) - max(ax, bx) > 2 and min(ay + ah, by + bh) - max(ay, by) > 2
                        self.assertFalse(overlap, (size, step, mode, text_of(a), text_of(b)))

    def test_no_new_clipping_or_truncation(self):
        found = set()
        for size in SIZES:
            for step, mode in VIEWS:
                self.show(size, step, mode)
                found |= collect_issues(self.app, size, step, mode)
        new = sorted(found - KNOWN_CLIPPING)
        self.assertEqual(new, [], "new layout defects:\n" + "\n".join(map(str, new)))


if __name__ == "__main__":
    if "--print" in sys.argv:
        UiLayout.setUpClass()
        found = set()
        probe = UiLayout()
        for size in SIZES:
            for step, mode in VIEWS:
                probe.show(size, step, mode)
                found |= collect_issues(UiLayout.app, size, step, mode)
        for item in sorted(found):
            print("   ", item, ",")
        UiLayout.tearDownClass()
    else:
        unittest.main()
