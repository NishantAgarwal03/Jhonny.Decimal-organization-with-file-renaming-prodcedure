"""The "Sorting settings" window: the few numbers a user may change, each with its range and what happens at the extremes.

Opened from the Organize panel. Apply hands a validated SortSettings to the caller; nothing is moved by this window.
The two safeguards (files with no usable name or folder are never guessed; a file follows its folder only if its type fits)
are not shown here: they are always on (johnny/core/sorting.py).
"""
import tkinter as tk
from dataclasses import replace
from tkinter import ttk

from johnny.core import sorting

BG, TEXT, MUTED, ACCENT, DANGER = "#f4f6f5", "#1f2a24", "#5f6f66", "#25423a", "#b42318"

HELP = {
    "lead": ("Lead", "How far ahead the best bucket must be of the second-best before a file is sorted. Range 0.000 to 0.050. "
             "Lower sorts more files but risks wrong folders; below 0.005 nearly every file is forced into a bucket. "
             "Strict first pass 0.030, normal second pass 0.010."),
    "vote": ("Folder vote", "Files the model refused follow their folder when most of the folder agrees. Off is the most careful "
             "setting, but far fewer files get sorted."),
    "share": ("Vote bar", "How much of a folder must agree before its refused files follow it. Range 50 to 100 percent. "
              "Below 70 percent is risky: a folder with a consistent mistake spreads it to every file. Strict 90, normal 80."),
    "min_files": ("Smallest folder that votes", "Range 5 to 200 files. Small folders give thin evidence. Default 20."),
}


class SortSettingsWindow(tk.Toplevel):
    def __init__(self, parent, current, on_apply):
        super().__init__(parent)
        self.title("Sorting settings")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        self.on_apply = on_apply
        self.presets = sorting.presets()

        self.lead = tk.StringVar()
        self.vote = tk.BooleanVar()
        self.share = tk.StringVar()
        self.min_files = tk.StringVar()
        self.message = tk.StringVar()
        self._current = current
        self._build()
        self.load(current)

    def _build(self):
        pad = {"padx": 18}
        tk.Label(self, text="Sorting settings", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 15)).pack(anchor="w", pady=(16, 2), **pad)
        tk.Label(self, text="Change a number, click Apply, then run Preview Organization to see the effect. Nothing is moved until you "
                 "click Apply Organization.", bg=BG, fg=MUTED, font=("Segoe UI", 10), wraplength=540, justify="left").pack(anchor="w", **pad)

        presets = tk.Frame(self, bg=BG)
        presets.pack(anchor="w", pady=(12, 4), **pad)
        for key, label in (("first", "First pass (strict)"), ("second", "Second pass (normal)")):
            p = self.presets[key]
            tk.Button(presets, text=f"{label}: lead {p.lead:.3f}, vote {p.vote_share:.0%}", relief="flat", bg="#edf2ef", fg=TEXT,
                      font=("Segoe UI", 10), command=lambda k=key: self.load(self.presets[k])).pack(side="left", padx=(0, 8), ipadx=6, ipady=3)

        self._row("lead", lambda f: ttk.Spinbox(f, textvariable=self.lead, from_=sorting.LIMITS["lead"][0], to=sorting.LIMITS["lead"][1], increment=0.005, format="%.3f", width=8))
        self._row("vote", lambda f: tk.Checkbutton(f, text="on", variable=self.vote, bg=BG, activebackground=BG, font=("Segoe UI", 10)))
        self._row("share", lambda f: ttk.Spinbox(f, textvariable=self.share, from_=50, to=100, increment=5, width=8))
        self._row("min_files", lambda f: ttk.Spinbox(f, textvariable=self.min_files, from_=sorting.LIMITS["min_files"][0], to=sorting.LIMITS["min_files"][1], increment=5, width=8))

        tk.Label(self, textvariable=self.message, bg=BG, fg=DANGER, font=("Segoe UI", 10), wraplength=540, justify="left").pack(anchor="w", pady=(8, 0), **pad)
        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", pady=(10, 16), **pad)
        self.apply_button = tk.Button(buttons, text="Apply", relief="flat", bg=ACCENT, fg="white", font=("Segoe UI Semibold", 10), command=self.apply)
        self.apply_button.pack(side="left", ipadx=16, ipady=4)
        tk.Button(buttons, text="Cancel", relief="flat", bg="#edf2ef", fg=TEXT, font=("Segoe UI", 10), command=self.destroy).pack(side="left", padx=8, ipadx=12, ipady=4)

    def _row(self, key, make_widget):
        title, help_text = HELP[key]
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=18, pady=(10, 0))
        head = tk.Frame(row, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text=title, bg=BG, fg=TEXT, font=("Segoe UI Semibold", 11), width=24, anchor="w").pack(side="left")
        make_widget(head).pack(side="left")
        tk.Label(row, text=help_text, bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=540, justify="left").pack(anchor="w", pady=(2, 0))

    def load(self, settings):
        self.lead.set(f"{settings.lead:.3f}")
        self.vote.set(settings.vote)
        self.share.set(f"{round(settings.vote_share * 100):d}")
        self.min_files.set(str(settings.min_files))
        self.message.set("")

    def read(self):
        """-> (SortSettings, "") or (None, a message). A value outside its range is refused, not silently changed."""
        try:
            lead, share, min_files = float(self.lead.get()), float(self.share.get()) / 100, int(float(self.min_files.get()))
        except ValueError:
            return None, "Every number must be a plain number."
        for name, value, label in (("lead", lead, "Lead"), ("vote_share", share, "Vote bar"), ("min_files", min_files, "Smallest folder")):
            low, high = sorting.LIMITS[name]
            if not low <= value <= high:
                shown = (f"{low * 100:g} to {high * 100:g} percent" if name == "vote_share" else f"{low:g} to {high:g}")
                return None, f"{label} must be between {shown}."
        return replace(self._current, lead=lead, vote=self.vote.get(), vote_share=share, min_files=min_files), ""

    def apply(self):
        settings, message = self.read()
        self.message.set(message)
        if settings is None:
            return
        self.on_apply(settings)
        self.destroy()


def open_sort_settings(parent, current, on_apply):
    return SortSettingsWindow(parent, current, on_apply)
