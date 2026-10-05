import csv
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from johnny.rename.filename_standardizer import FilenameStandardizer
from johnny.core.evidence_engine import group_leftovers, summarize_closest, write_leftover_handoff_json
from johnny.core.index_manager import IndexManager
from johnny.core.nlp_engine import NLPEngine
from johnny.core import sorting
from johnny.core.planner import SPECIAL_DIRS, is_managed_dir
from johnny.core.utils import default_sorted_root
from johnny.core.sorting import plan_text
from johnny.rename.rename_engine import ContextualRenamer
from johnny.organize.router import Router
from johnny.ui.sort_settings_window import open_sort_settings


APP_TITLE = "Johnny Organizer Prototype"
APP_BG = "#f2f5f3"
PANEL_BG = "#ffffff"
SUBTLE_BG = "#f8fbf9"
ACCENT = "#223a31"
ACCENT_SOFT = "#eef4f1"
TEXT = "#21312b"
MUTED = "#6f7e77"
SUCCESS = "#4f7f6a"
PROJECT_DIR = Path(__file__).resolve().parents[2]  # repo root


def safe_session_name(target_path: str) -> str:
    base_name = os.path.basename(os.path.normpath(target_path))
    if not base_name or base_name in {".", os.sep}:
        base_name = target_path.replace(":\\", "_Drive").replace(":", "_Drive").replace("\\", "_").replace("/", "_")
    return f"session_{base_name}"


def iter_files(target_path: str, include_subfolders: bool):
    target_path = os.path.abspath(target_path)
    if os.path.isfile(target_path):
        yield target_path
        return
    if include_subfolders:
        for root, dirs, files in os.walk(target_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and not is_managed_dir(d)]
            for name in files:
                if name.startswith("."):
                    continue
                yield os.path.join(root, name)
    else:
        for name in sorted(os.listdir(target_path)):
            path = os.path.join(target_path, name)
            if os.path.isfile(path) and not name.startswith("."):
                yield path


def count_folders_in_scope(target_path: str, include_subfolders: bool) -> int:
    target_path = os.path.abspath(target_path)
    if os.path.isfile(target_path):
        return 0
    if not os.path.isdir(target_path):
        return 0
    if include_subfolders:
        count = 1
        for _, dirs, _ in os.walk(target_path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            count += len(dirs)
        return count
    return len([name for name in os.listdir(target_path) if os.path.isdir(os.path.join(target_path, name)) and not name.startswith(".")])


class PrototypeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1240x800")
        self.configure(bg=APP_BG)
        self.minsize(1100, 700)

        self.target_path = tk.StringVar()
        self.include_subfolders = tk.BooleanVar(value=False)
        self.mode_label = tk.StringVar(value="Preview only")
        self.index_path = tk.StringVar(value=str(Path("master_index.json").resolve()))
        self.status_text = tk.StringVar(value="")
        self.summary_stats = {
            "Files": tk.StringVar(value="0"),
            "Folders": tk.StringVar(value="0"),
            "Weak names": tk.StringVar(value="0"),
        }
        self.current_step = tk.StringVar(value="Scan")
        self.external_llm_mode = False

        self.results_queue: queue.Queue = queue.Queue()
        self.worker = None
        self.current_preview_rows = []
        self.folder_vote_moved = 0
        self.sort_stats = {}
        self.sort_settings = sorting.presets()["first"]   # the strict first pass until the user changes it
        self.organize_preview_state = {
            "Category Plan": {"rows": [], "reason_header": "Reason", "open_path": None, "alert_message": ""},
            "Apply Organization": {"rows": [], "reason_header": "Reason", "open_path": None, "alert_message": ""},
        }

        self.standardizer = FilenameStandardizer()

        self._configure_style()
        self._build_layout()
        self._show_step("Scan")
        self.after(150, self._poll_queue)

    def _configure_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=APP_BG)
        style.configure("Panel.TFrame", background=PANEL_BG)
        style.configure("Subtle.TFrame", background=SUBTLE_BG)
        style.configure("TLabel", background=APP_BG, foreground=TEXT, font=("Segoe UI", 11))
        style.configure("Panel.TLabel", background=PANEL_BG, foreground=TEXT, font=("Segoe UI", 11))
        style.configure("Muted.TLabel", background=APP_BG, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("PanelMuted.TLabel", background=PANEL_BG, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("Header.TLabel", background=APP_BG, foreground=TEXT, font=("Segoe UI Semibold", 22))
        style.configure("Section.TLabel", background=PANEL_BG, foreground=TEXT, font=("Segoe UI Semibold", 15))
        style.configure("SmallValue.TLabel", background=ACCENT_SOFT, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Sidebar.TFrame", background=SUBTLE_BG)
        style.configure("Sidebar.TLabel", background=SUBTLE_BG, foreground=TEXT)
        style.configure("Treeview", font=("Segoe UI", 10), rowheight=28)
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))
        style.map("Treeview", background=[("selected", "#dce8e2")], foreground=[("selected", TEXT)])

    def _build_layout(self):
        shell = tk.Frame(self, bg=PANEL_BG, highlightbackground="#dce4df", highlightthickness=1)
        shell.pack(fill="both", expand=True, padx=18, pady=18)

        self.sidebar = tk.Frame(shell, bg=SUBTLE_BG, highlightbackground="#e2e8e4", highlightthickness=1, width=240)
        self.sidebar.pack(side="left", fill="y", padx=20, pady=20)
        self.sidebar.pack_propagate(False)

        tk.Label(self.sidebar, text="Johnny Organizer", bg=SUBTLE_BG, fg=TEXT, font=("Segoe UI Semibold", 16)).pack(anchor="w", padx=18, pady=(20, 0))
        tk.Label(self.sidebar, text="Workflow", bg=SUBTLE_BG, fg=MUTED, font=("Segoe UI", 12)).pack(anchor="w", padx=20, pady=(0, 24))

        self.step_buttons = {}
        steps = [
            ("Scan", "Create File Log"),
            ("Rename", "Quick  |  Deep"),
            ("Organize", "Category Plan  |  Apply"),
        ]
        for step, desc in steps:
            btn = tk.Button(
                self.sidebar,
                text=f"{step}\n{desc}",
                anchor="w",
                justify="left",
                relief="flat",
                bg="white",
                fg=TEXT,
                activebackground=ACCENT_SOFT,
                activeforeground=TEXT,
                font=("Segoe UI", 11),
                command=lambda s=step: self._show_step(s),
            )
            btn.pack(fill="x", padx=18, pady=(0, 16), ipady=14)
            self.step_buttons[step] = btn

        faq = tk.Frame(self.sidebar, bg="white", highlightbackground="#e2e8e4", highlightthickness=1)
        faq.pack(side="bottom", fill="x", padx=18, pady=18)
        tk.Label(faq, text="Help / FAQ", bg="white", fg=TEXT, font=("Segoe UI Semibold", 13)).pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(faq, text="Preview never changes files.", bg="white", fg=MUTED, font=("Segoe UI", 10), wraplength=180, justify="left").pack(anchor="w", padx=16, pady=(6, 12))

        self.main = tk.Frame(shell, bg=APP_BG)
        self.main.pack(side="left", fill="both", expand=True, padx=(0, 20), pady=20)

        header = tk.Frame(self.main, bg=APP_BG)
        header.pack(fill="x", padx=10, pady=(0, 10))
        tk.Label(header, textvariable=self.current_step, bg=APP_BG, fg=TEXT, font=("Segoe UI Semibold", 28)).pack(anchor="w")
        self.step_subtitle = tk.Label(header, text="", bg=APP_BG, fg=MUTED, font=("Segoe UI", 12))
        self.step_subtitle.pack(anchor="w", padx=2)

        self.summary_bar = tk.Frame(self.main, bg=ACCENT_SOFT, highlightbackground="#d6e3dc", highlightthickness=1)
        self.summary_bar.pack(fill="x", padx=10, pady=(0, 14))

        self.summary_stats_row = tk.Frame(self.summary_bar, bg=ACCENT_SOFT)
        self.summary_stats_row.pack(side="right", padx=(8, 12), pady=10)
        self.stat_cards = []
        for label in ["Files", "Folders", "Weak names"]:
            card = tk.Frame(self.summary_stats_row, bg="white", width=92, height=58)
            card.pack(side="left", padx=6)
            card.pack_propagate(False)
            tk.Label(card, text=label, bg="white", fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=10, pady=(8, 0))
            tk.Label(card, textvariable=self.summary_stats[label], bg="white", fg=TEXT, font=("Segoe UI Semibold", 10)).pack(anchor="w", padx=10, pady=(4, 0))
            self.stat_cards.append(card)

        summary_left = tk.Frame(self.summary_bar, bg=ACCENT_SOFT)
        summary_left.pack(side="left", fill="both", expand=True, padx=(18, 8), pady=10)
        tk.Label(summary_left, text="This operation affects", bg=ACCENT_SOFT, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w")
        self.target_value = tk.Label(summary_left, text="No folder selected", bg=ACCENT_SOFT, fg=TEXT, font=("Segoe UI Semibold", 14))
        self.target_value.pack(anchor="w", pady=(2, 0))
        self.scope_value = tk.Label(summary_left, text="Scope: This folder only | Subfolders will not be changed | Mode: Preview only", bg=ACCENT_SOFT, fg=SUCCESS, font=("Segoe UI", 10), justify="left")
        self.scope_value.pack(anchor="w", pady=(6, 0))
        summary_left.bind("<Configure>", lambda e: self.scope_value.config(wraplength=max(e.width - 4, 200)))

        controls = tk.Frame(self.main, bg=APP_BG)
        controls.pack(fill="x", padx=10, pady=(0, 14))

        tk.Button(controls, text="Choose folder", command=self._choose_folder, bg=ACCENT, fg="white", relief="flat", font=("Segoe UI Semibold", 10)).pack(side="left", ipady=4)
        tk.Checkbutton(
            controls,
            text="Include subfolders",
            variable=self.include_subfolders,
            bg=APP_BG,
            fg=TEXT,
            activebackground=APP_BG,
            command=self._on_scope_change,
            font=("Segoe UI", 10),
        ).pack(side="left", padx=16)
        self.choose_index_button = tk.Button(
            controls,
            text="Choose JD index",
            command=self._choose_index,
            bg="#edf2ef",
            fg=TEXT,
            relief="flat",
            font=("Segoe UI Semibold", 10),
        )
        self.choose_index_button.pack(side="left", ipady=4)
        self.index_label = tk.Label(controls, text=os.path.basename(self.index_path.get()), bg=APP_BG, fg=MUTED, font=("Segoe UI", 10))
        self.index_label.pack(side="left", padx=12)
        self.sort_button = tk.Button(controls, text="Sorting settings...", command=self._open_sort_settings, bg="#edf2ef", fg=TEXT, relief="flat", font=("Segoe UI Semibold", 10))
        self.status_label = tk.Label(controls, textvariable=self.status_text, bg=APP_BG, fg="#b42318", font=("Segoe UI Semibold", 10))
        self.status_label.pack(side="right")

        self.footer_note = tk.Label(
            self.main,
            text="Rename and Organize use the same target and scope when you move to those steps.",
            bg=APP_BG,
            fg=MUTED,
            font=("Segoe UI", 10),
        )
        self.footer_note.pack(side="bottom", anchor="w", padx=12, pady=(8, 0))

        content = tk.Frame(self.main, bg=APP_BG)
        content.pack(fill="both", expand=True, padx=10)
        content.columnconfigure(0, weight=11)
        content.columnconfigure(1, weight=9)
        content.rowconfigure(0, weight=1)

        self.left_panel = tk.Frame(content, bg=PANEL_BG, highlightbackground="#e4eae6", highlightthickness=1)
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.right_panel = tk.Frame(content, bg=SUBTLE_BG, highlightbackground="#e4eae6", highlightthickness=1)
        self.right_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        preview_head = tk.Frame(self.right_panel, bg=SUBTLE_BG)
        preview_head.pack(fill="x", padx=20, pady=(18, 0))
        self.preview_title = tk.Label(preview_head, text="Preview Summary", bg=SUBTLE_BG, fg=TEXT, font=("Segoe UI Semibold", 15))
        self.preview_title.pack(anchor="w")
        self.preview_hint = tk.Label(preview_head, text="Preview does not change any files.", bg=SUBTLE_BG, fg=SUCCESS, font=("Segoe UI", 10))
        self.preview_hint.pack(anchor="w", pady=(2, 0))

        preview_actions = tk.Frame(self.right_panel, bg=SUBTLE_BG)
        preview_actions.pack(side="bottom", fill="x", padx=20, pady=(0, 16))
        self.preview_open_button = tk.Button(preview_actions, text="View output", relief="flat", bg="#edf2ef", fg=TEXT, command=self._open_last_output, state="disabled")
        self.preview_open_button.pack(side="left", ipadx=10, ipady=6)

        table_wrap = tk.Frame(self.right_panel, bg=SUBTLE_BG)
        table_wrap.pack(fill="both", expand=True, padx=20, pady=(12, 8))

        self.preview_table = ttk.Treeview(table_wrap, columns=("current", "proposed", "reason"), show="headings")
        self.preview_table.heading("current", text="Current")
        self.preview_table.heading("proposed", text="Proposed / Output")
        self.preview_table.heading("reason", text="Reason")
        self.preview_table.column("current", width=100, minwidth=70, anchor="w", stretch=True)
        self.preview_table.column("proposed", width=100, minwidth=70, anchor="w", stretch=True)
        self.preview_table.column("reason", width=60, minwidth=50, anchor="w", stretch=True)
        preview_v_scroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.preview_table.yview)
        preview_h_scroll = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.preview_table.xview)
        self.preview_table.configure(yscrollcommand=preview_v_scroll.set, xscrollcommand=preview_h_scroll.set)
        preview_h_scroll.pack(side="bottom", fill="x")
        preview_v_scroll.pack(side="right", fill="y")
        self.preview_table.pack(side="left", fill="both", expand=True)

        self.last_output_path = None

        self._build_scan_panel()
        self._build_rename_panel()
        self._build_organize_panel()

    def _action_bar(self, panel):
        bar = tk.Frame(panel, bg=PANEL_BG)
        bar.pack(side="bottom", fill="x", padx=24, pady=(0, 16))
        return bar

    def _build_scan_panel(self):
        self.scan_panel = tk.Frame(self.left_panel, bg=PANEL_BG)
        action_bar = self._action_bar(self.scan_panel)
        body = tk.Frame(self.scan_panel, bg=PANEL_BG)
        body.pack(fill="both", expand=True, padx=24, pady=(22, 12))

        tk.Label(body, text="Create File Log", bg=PANEL_BG, fg=TEXT, font=("Segoe UI Semibold", 18)).pack(anchor="w")
        tk.Label(body, text="What this does", bg=PANEL_BG, fg=TEXT, font=("Segoe UI", 12)).pack(anchor="w", pady=(10, 6))
        scan_lines = [
            "Scans the selected folder using the current scope",
            "Creates a full inventory log without changing files",
            "Prepares the next steps for Rename and Organize",
        ]
        for line in scan_lines:
            tk.Label(body, text=f"• {line}", bg=PANEL_BG, fg=MUTED, font=("Segoe UI", 10), justify="left", wraplength=300).pack(anchor="w", padx=2, pady=3)
        tk.Label(body, text="Output files", bg=PANEL_BG, fg=TEXT, font=("Segoe UI", 12)).pack(anchor="w", pady=(16, 4))
        self.scan_output_label = tk.Label(body, text="No output created yet", bg=PANEL_BG, fg=MUTED, justify="left", anchor="w", font=("Segoe UI", 10), wraplength=300)
        self.scan_output_label.pack(anchor="w")
        tk.Button(action_bar, text="Create File Log", bg=ACCENT, fg="white", relief="flat", font=("Segoe UI Semibold", 11), command=self._scan_create_log).pack(side="left", ipadx=18, ipady=8)

    def _build_rename_panel(self):
        self.rename_panel = tk.Frame(self.left_panel, bg=PANEL_BG)
        action_bar = self._action_bar(self.rename_panel)
        body = tk.Frame(self.rename_panel, bg=PANEL_BG)
        body.pack(fill="both", expand=True, padx=24, pady=(22, 12))

        tk.Label(body, text="Rename Files", bg=PANEL_BG, fg=TEXT, font=("Segoe UI Semibold", 18)).pack(anchor="w")
        self.rename_mode = tk.StringVar(value="Quick")
        tabs = tk.Frame(body, bg=PANEL_BG)
        tabs.pack(anchor="w", pady=(10, 12))
        for idx, mode in enumerate(["Quick", "Deep"]):
            tk.Radiobutton(
                tabs,
                text=mode,
                variable=self.rename_mode,
                value=mode,
                indicatoron=False,
                width=10,
                command=self._refresh_rename_copy,
                bg="#edf2ef",
                selectcolor=ACCENT_SOFT,
                relief="flat",
                font=("Segoe UI Semibold", 10),
            ).grid(row=0, column=idx, padx=(0, 10))
        self.rename_desc = tk.Label(body, text="", bg=PANEL_BG, fg=MUTED, justify="left", font=("Segoe UI", 10), wraplength=300)
        self.rename_desc.pack(anchor="w")
        self.rename_preview_button = tk.Button(action_bar, text="Preview Rename", bg="#edf2ef", fg=TEXT, relief="flat", font=("Segoe UI Semibold", 11), command=self._rename_preview)
        self.rename_preview_button.pack(side="left", ipadx=8, ipady=8)
        self.rename_apply_button = tk.Button(action_bar, text="Apply Rename", bg=ACCENT, fg="white", relief="flat", font=("Segoe UI Semibold", 11), command=self._rename_apply)
        self.rename_apply_button.pack(side="left", padx=(10, 0), ipadx=8, ipady=8)
        self._refresh_rename_copy()

    def _build_organize_panel(self):
        self.organize_panel = tk.Frame(self.left_panel, bg=PANEL_BG)
        action_bar = self._action_bar(self.organize_panel)
        body = tk.Frame(self.organize_panel, bg=PANEL_BG)
        body.pack(fill="both", expand=True, padx=24, pady=(22, 12))

        tk.Label(body, text="Organize Files", bg=PANEL_BG, fg=TEXT, font=("Segoe UI Semibold", 18)).pack(anchor="w")
        self.organize_mode = tk.StringVar(value="Category Plan")
        tabs = tk.Frame(body, bg=PANEL_BG)
        tabs.pack(anchor="w", pady=(10, 12))
        for idx, mode in enumerate(["Category Plan", "Apply Organization"]):
            tk.Radiobutton(
                tabs,
                text=mode,
                variable=self.organize_mode,
                value=mode,
                indicatoron=False,
                width=17,
                command=self._refresh_organize_copy,
                bg="#edf2ef",
                selectcolor=ACCENT_SOFT,
                relief="flat",
                font=("Segoe UI Semibold", 10),
            ).grid(row=0, column=idx, padx=(0, 10))
        self.organize_desc = tk.Label(body, text="", bg=PANEL_BG, fg=MUTED, justify="left", font=("Segoe UI", 10), wraplength=300)
        self.organize_desc.pack(anchor="w")
        self.organize_secondary_button = tk.Button(
            action_bar,
            text="Create Plan",
            bg="#edf2ef",
            fg=TEXT,
            relief="flat",
            font=("Segoe UI Semibold", 11),
            command=self._organize_preview,
        )
        self.organize_secondary_button.pack(side="left", ipadx=8, ipady=8)
        self.organize_primary_button = tk.Button(
            action_bar,
            text="Open Index",
            bg=ACCENT,
            fg="white",
            relief="flat",
            font=("Segoe UI Semibold", 11),
            command=self._open_index_file,
        )
        self.organize_primary_button.pack(side="left", padx=(10, 0), ipadx=8, ipady=8)
        self._refresh_organize_copy()

    def _show_step(self, step):
        self.current_step.set(step)
        subtitles = {
            "Scan": "Create a complete inventory before renaming or organizing anything.",
            "Rename": "Use Quick or Deep rename based on how much context you want to use.",
            "Organize": "Review your category plan, then apply Johnny.Decimal organization.",
        }
        self.step_subtitle.config(text=subtitles[step])
        for name, btn in self.step_buttons.items():
            btn.configure(bg="#eef5f1" if name == step else "white")
        for widget in self.left_panel.winfo_children():
            widget.pack_forget()
        if step == "Scan":
            self.scan_panel.pack(fill="both", expand=True)
        elif step == "Rename":
            self.rename_panel.pack(fill="both", expand=True)
        else:
            self.organize_panel.pack(fill="both", expand=True)
        self._refresh_target_summary()
        self._update_index_picker_state()
        if step == "Organize":
            self.sort_button.pack(side="left", padx=(0, 12), ipady=4, before=self.status_label)
        else:
            self.sort_button.pack_forget()

    def _refresh_rename_copy(self):
        if self.rename_mode.get() == "Quick":
            text = (
                "Uses the existing filename only.\n"
                "Fast and safe for large folders.\n"
                "Leaves weak names unchanged for the deep pass."
            )
        else:
            text = (
                "Uses filename plus local semantic context.\n"
                "Better for weak or vague names.\n"
                "Slower, but more precise than Quick rename."
            )
        self.rename_desc.config(text=text)

    def _refresh_organize_copy(self):
        if self.organize_mode.get() == "Category Plan":
            text = (
                "Build the raw category-plan artifacts for LLM review.\n"
                "Use this when you need a fresh JD plan for the target folder.\n"
                "This step does not move files."
            )
            self.organize_secondary_button.config(text="Create Plan", command=self._create_plan, bg="#edf2ef", fg=TEXT)
            self.organize_primary_button.config(text="Open Index", command=self._open_index_file, bg=ACCENT, fg="white")
        else:
            text = (
                "Routes files into JD folders using the selected index.\n"
                "Use preview first to inspect sample category placement.\n"
                "Apply will move files on disk."
            )
            self.organize_secondary_button.config(text="Preview Organization", command=self._organize_preview, bg="#edf2ef", fg=TEXT)
            self.organize_primary_button.config(text="Apply Organization", command=self._organize_apply, bg=ACCENT, fg="white")
        self.organize_desc.config(text=text)
        self._update_index_picker_state()
        self._restore_organize_preview_state()

    def _update_index_picker_state(self):
        enabled = (
            self.current_step.get() == "Organize"
            and getattr(self, "organize_mode", None)
            and self.organize_mode.get() == "Category Plan"
            and self.external_llm_mode
        )
        if enabled:
            self.choose_index_button.config(state="normal", bg="#edf2ef", fg=TEXT, activebackground="#dfe8e2", cursor="hand2")
        else:
            self.choose_index_button.config(state="disabled", bg="#eef1ef", fg="#9aa7a0", activebackground="#eef1ef", cursor="arrow")

    def _choose_folder(self):
        path = filedialog.askdirectory(title="Choose target folder")
        if path:
            self.target_path.set(path)
            self._refresh_target_summary()
            self._estimate_target_stats()

    def _choose_index(self):
        if not (self.current_step.get() == "Organize" and self.organize_mode.get() == "Category Plan" and self.external_llm_mode):
            return
        path = filedialog.askopenfilename(
            title="Choose JD index",
            filetypes=[
                ("Index files", ("*.json", "*.txt")),
                ("JSON files", "*.json"),
                ("Text files", "*.txt"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.index_path.set(path)
            self.index_label.config(text=os.path.basename(path))

    def _on_scope_change(self):
        self._refresh_target_summary()
        self._estimate_target_stats()

    def _refresh_target_summary(self):
        target = self.target_path.get().strip()
        self.target_value.config(text=target or "No folder selected")
        scope_text = "Include subfolders" if self.include_subfolders.get() else "This folder only"
        detail = "Subfolders will be changed" if self.include_subfolders.get() else "Subfolders will not be changed"
        self.scope_value.config(text=f"Scope: {scope_text}   |   {detail}   |   Mode: {self.mode_label.get()}")

    def _estimate_target_stats(self):
        target = self.target_path.get().strip()
        if not target or not os.path.exists(target):
            self.summary_stats["Files"].set("0")
            self.summary_stats["Folders"].set("0")
            self.summary_stats["Weak names"].set("0")
            return
        files = 0
        weak = 0
        for path in iter_files(target, self.include_subfolders.get()):
            files += 1
            if self.standardizer.should_attempt_standardization(os.path.basename(path)):
                weak += 1
        folders = count_folders_in_scope(target, self.include_subfolders.get())
        self.summary_stats["Files"].set(str(files))
        self.summary_stats["Folders"].set(str(folders))
        self.summary_stats["Weak names"].set(str(weak))

    def _require_target(self) -> str | None:
        target = self.target_path.get().strip()
        if not target or not os.path.exists(target):
            messagebox.showwarning(APP_TITLE, "Choose a valid target folder first.")
            return None
        return target

    def _open_last_output(self):
        if self.last_output_path and os.path.exists(self.last_output_path):
            os.startfile(self.last_output_path)

    def _export_handoff_copy(self, source_path: str):
        suggested = Path(source_path).name
        destination = filedialog.asksaveasfilename(
            title="Save raw LLM handoff",
            defaultextension=".json",
            initialfile=suggested,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not destination:
            return False
        Path(destination).write_bytes(Path(source_path).read_bytes())
        os.startfile(destination)
        return True

    def _handle_plan_ready(self, payload):
        self._cache_organize_preview_state(
            "Category Plan",
            payload["rows"],
            reason_header=payload.get("reason_header", "Reason"),
            open_path=payload.get("open_path"),
            alert_message=payload.get("alert_message", ""),
        )
        self.last_output_path = payload.get("open_path")
        self.preview_open_button.config(state="normal" if self.last_output_path else "disabled")
        self._set_preview_rows(payload["rows"], reason_header=payload.get("reason_header", "Reason"))
        self._set_index_alert("")
        self._update_apply_button_state()

        handoff_path = payload.get("open_path")
        if not handoff_path or not os.path.exists(handoff_path):
            return

        choice = messagebox.askyesnocancel(
            APP_TITLE,
            "Use connected LLM now, or choose No to save the handoff for an external LLM.",
        )
        if choice is True:
            self.external_llm_mode = False
            self._update_index_picker_state()
            os.startfile(handoff_path)
        elif choice is False:
            self.external_llm_mode = True
            self._update_index_picker_state()
            self._export_handoff_copy(handoff_path)
        else:
            self.external_llm_mode = False
            self._update_index_picker_state()

    def _merge_source_into_master_index(self, source_index_path: str) -> str:
        runtime_source = source_index_path
        if source_index_path.lower().endswith(".txt"):
            runtime_source = self._build_json_index_from_txt(source_index_path)

        runtime_source_path = Path(runtime_source).resolve()
        master_path = Path("master_index.json").resolve()

        if runtime_source_path == master_path:
            idx = IndexManager(str(master_path))
            if not idx.get_category_prefix("Unsorted_Miscellaneous"):
                idx.data["categories"]["Unsorted_Miscellaneous"] = "80.01"
                idx._save()
            return str(master_path)

        source_data = json.loads(runtime_source_path.read_text(encoding="utf-8"))
        master_idx = IndexManager(str(master_path), create=True)

        source_categories = source_data.get("categories", {})
        for topic, code in source_categories.items():
            master_idx.data.setdefault("categories", {})
            master_idx.data["categories"][topic] = code

        master_idx.data.setdefault("incubation", {})
        source_incubation = source_data.get("incubation", {})
        for topic, items in source_incubation.items():
            existing = master_idx.data["incubation"].setdefault(topic, [])
            for item in items:
                if item not in existing:
                    existing.append(item)

        source_profiles = source_data.get("profiles")
        if isinstance(source_profiles, dict):
            master_idx.data.setdefault("profiles", {}).update({k: v for k, v in source_profiles.items() if isinstance(v, dict)})

        master_idx.data["categories"].setdefault("Unsorted_Miscellaneous", "80.01")
        master_idx._save()
        return str(master_path)

    def _open_index_file(self):
        index_path = self.index_path.get().strip()
        if index_path and os.path.exists(index_path):
            master_path = self._merge_source_into_master_index(index_path)
            self.index_path.set(master_path)
            self.index_label.config(text=os.path.basename(master_path))
            os.startfile(master_path)
        else:
            messagebox.showwarning(APP_TITLE, "Choose a valid JD index file first.")

    def _ensure_text_index_has_unsorted(self, index_path: str):
        try:
            text = Path(index_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = Path(index_path).read_text(encoding="utf-8", errors="ignore")
        if "unsorted_miscellaneous" in text.lower():
            return
        suffix = "" if text.endswith(("\n", "\r")) or not text else "\n"
        Path(index_path).write_text(f"{text}{suffix}80.01 Unsorted_Miscellaneous\n", encoding="utf-8")

    def _normalize_topic_name(self, raw_topic: str) -> str:
        topic = re.sub(r"[^A-Za-z0-9]+", "_", (raw_topic or "").strip())
        topic = re.sub(r"_+", "_", topic).strip("_")
        return topic or "Unsorted_Miscellaneous"

    def _extract_categories_from_structured_plan(self, data):
        categories = {}

        top_categories = data.get("categories", [])
        if not isinstance(top_categories, list):
            return categories

        for group in top_categories:
            if not isinstance(group, dict):
                continue

            group_name = group.get("name", "")
            group_id = str(group.get("id", "")).strip()
            subcategories = group.get("subcategories", [])

            if group_name and group_id and "-" in group_id:
                group_code = group_id.split("-")[0]
                if group_code.isdigit():
                    categories[self._normalize_topic_name(group_name)] = f"{int(group_code):02d}.01"

            if not isinstance(subcategories, list):
                continue

            next_minor = 1
            for subcat in subcategories:
                if not isinstance(subcat, dict):
                    continue
                sub_name = subcat.get("name", "")
                sub_id = str(subcat.get("id", "")).strip()
                normalized_name = self._normalize_topic_name(sub_name)
                if not normalized_name:
                    continue

                code = None
                if group_id and "-" in group_id:
                    area = group_id.split("-")[0]
                    if area.isdigit():
                        if sub_id.isdigit():
                            code = f"{int(area):02d}.{int(sub_id):02d}"
                        else:
                            code = f"{int(area):02d}.{next_minor:02d}"
                elif sub_id.isdigit():
                    code = f"{int(sub_id):02d}.01"

                if code is None:
                    code = f"10.{next_minor:02d}"

                if normalized_name not in categories:
                    categories[normalized_name] = code
                    next_minor += 1

        categories.setdefault("Unsorted_Miscellaneous", "80.01")
        return categories

    def _normalized_profiles(self, plan):
        """Optional top-level "profiles" of an LLM plan, keyed by the same normalized names as the categories."""
        raw = plan.get("profiles") if isinstance(plan, dict) else None
        if not isinstance(raw, dict):
            return {}
        return {self._normalize_topic_name(name): profile for name, profile in raw.items() if isinstance(profile, dict)}

    def _build_json_index_from_txt(self, txt_path: str) -> str:
        raw_text = Path(txt_path).read_text(encoding="utf-8", errors="ignore")
        categories = {}

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            parsed = None
        profiles = self._normalized_profiles(parsed)

        if isinstance(parsed, dict) and isinstance(parsed.get("categories"), list):
            categories = self._extract_categories_from_structured_plan(parsed)
        else:
            self._ensure_text_index_has_unsorted(txt_path)
            lines = raw_text.splitlines()
            next_area = 10
            next_minor = 1
            for raw_line in lines:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                match = re.match(r"^\s*(\d{2}\.\d{2})\s*[-: ]+\s*(.+?)\s*$", line)
                if match:
                    code, topic = match.groups()
                    categories[self._normalize_topic_name(topic)] = code
                    continue
                topic = self._normalize_topic_name(line)
                if topic in categories:
                    continue
                categories[topic] = f"{next_area:02d}.{next_minor:02d}"
                next_minor += 1
                if next_minor > 99:
                    next_area += 1
                    next_minor = 1
            categories.setdefault("Unsorted_Miscellaneous", "80.01")

        json_path = str(Path(txt_path).with_suffix(".json"))
        index = {"categories": categories, "incubation": {}}
        if profiles:
            index["profiles"] = profiles
        Path(json_path).write_text(json.dumps(index, indent=2), encoding="utf-8")
        return json_path

    def _resolve_index_path_for_runtime(self) -> str:
        index_path = self.index_path.get().strip()
        if not index_path:
            raise ValueError("Choose a JD index file first.")
        if index_path.lower().endswith(".txt"):
            return self._build_json_index_from_txt(index_path)
        idx = IndexManager(index_path)
        if not idx.get_category_prefix("Unsorted_Miscellaneous"):
            idx.data["categories"]["Unsorted_Miscellaneous"] = "80.01"
            idx._save()
        return index_path

    def _set_preview_rows(self, rows, reason_header="Reason"):
        for item in self.preview_table.get_children():
            self.preview_table.delete(item)
        self.preview_table.heading("reason", text=reason_header)
        self.current_preview_rows = rows
        for row in rows[:500]:
            self.preview_table.insert("", "end", values=row)

    def _cache_organize_preview_state(self, mode, rows, reason_header="Reason", open_path=None, alert_message=""):
        self.organize_preview_state[mode] = {
            "rows": rows,
            "reason_header": reason_header,
            "open_path": open_path,
            "alert_message": alert_message,
        }

    def _restore_organize_preview_state(self):
        state = self.organize_preview_state.get(self.organize_mode.get(), {})
        rows = state.get("rows") or []
        self.last_output_path = state.get("open_path")
        self.preview_open_button.config(state="normal" if self.last_output_path else "disabled")
        self._set_preview_rows(rows, reason_header=state.get("reason_header", "Reason"))
        self._set_index_alert(state.get("alert_message", ""))
        self._update_apply_button_state()

    def _set_index_alert(self, message=""):
        self.status_text.set(message)

    def _index_preview_failed(self, total_files, rows):
        # Software units and junk are placed by rules, not by the router, so they are not part of the guard.
        for row in rows:
            if str(row[0]) in SPECIAL_DIRS:
                try:
                    total_files -= int(row[1])
                except (TypeError, ValueError):
                    pass
        if total_files <= 0:
            return False
        allowed_unsorted = math.floor(total_files / 150)
        unsorted_count = 0
        for row in rows:
            folder_name = str(row[0])
            try:
                row_count = int(row[1])
            except (TypeError, ValueError):
                row_count = 0
            if "unsorted_miscellaneous" in folder_name.lower():
                unsorted_count += row_count
        return unsorted_count > allowed_unsorted

    def _update_apply_button_state(self):
        if self.organize_mode.get() != "Apply Organization":
            self.organize_primary_button.config(state="normal", bg=ACCENT, fg="white")
            return
        state = self.organize_preview_state.get("Apply Organization", {})
        rows = state.get("rows") or []
        failed = self._index_preview_failed(sum(int(row[1]) for row in rows if str(row[1]).isdigit()), rows) if rows else False
        if failed:
            self.organize_primary_button.config(state="disabled", bg="#cfd8d3", fg="#73817a")
        else:
            self.organize_primary_button.config(state="normal", bg=ACCENT, fg="white")

    def _run_async(self, worker, start_message):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_TITLE, "Please wait for the current action to finish.")
            return
        self._set_index_alert("")
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.results_queue.get_nowait()
                if kind == "status":
                    self._set_index_alert("")
                elif kind == "scan_done":
                    self.scan_output_label.config(text=payload["label"])
                    self.last_output_path = payload["open_path"]
                    self.preview_open_button.config(state="normal")
                    self._set_preview_rows(payload["rows"], reason_header="Type")
                    self.summary_stats["Files"].set(str(payload["files"]))
                    self.summary_stats["Folders"].set(str(payload["folders"]))
                    self.summary_stats["Weak names"].set("0")
                    self._set_index_alert(payload.get("alert_message", ""))
                    self._update_apply_button_state()
                elif kind == "preview":
                    if self.current_step.get() == "Organize":
                        self._cache_organize_preview_state(
                            self.organize_mode.get(),
                            payload["rows"],
                            reason_header=payload.get("reason_header", "Reason"),
                            open_path=payload.get("open_path"),
                            alert_message=payload.get("alert_message", ""),
                        )
                    self.last_output_path = payload.get("open_path")
                    self.preview_open_button.config(state="normal" if self.last_output_path else "disabled")
                    self._set_preview_rows(payload["rows"], reason_header=payload.get("reason_header", "Reason"))
                    self._set_index_alert(payload.get("alert_message", ""))
                    self._update_apply_button_state()
                elif kind == "plan_ready":
                    self._handle_plan_ready(payload)
                elif kind == "done":
                    self._set_index_alert("")
                elif kind == "error":
                    self._set_index_alert("")
                    messagebox.showerror(APP_TITLE, payload)
        except queue.Empty:
            pass
        self.after(150, self._poll_queue)

    def _scan_create_log(self):
        target = self._require_target()
        if not target:
            return
        self.mode_label.set("Preview only")
        self._refresh_target_summary()

        def worker():
            try:
                session_name = safe_session_name(target)
                session_dir = PROJECT_DIR / "runs" / session_name
                session_dir.mkdir(parents=True, exist_ok=True)
                out_csv = session_dir / "drive_scan.csv"
                files = 0
                rows = []
                with open(out_csv, "w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["path", "name", "type"])
                    for path in iter_files(target, self.include_subfolders.get()):
                        rel_path = os.path.relpath(path, target)
                        name, ext = os.path.splitext(os.path.basename(path))
                        writer.writerow([rel_path, name, ext.lower()])
                        files += 1
                        rows.append((rel_path, name, ext.lower()))
                folders = count_folders_in_scope(target, self.include_subfolders.get())
                label = f"drive_scan.csv\n{session_dir}"
                self.results_queue.put(("scan_done", {"label": label, "open_path": str(out_csv), "rows": rows[:500], "files": files, "folders": folders}))
            except Exception as exc:
                self.results_queue.put(("error", str(exc)))

        self._run_async(worker, "Creating file log...")

    def _rename_preview(self):
        target = self._require_target()
        if not target:
            return
        self.mode_label.set("Preview only")
        self._refresh_target_summary()

        def worker():
            try:
                rows = []
                reserved = set()
                if self.rename_mode.get() == "Quick":
                    for path in iter_files(target, self.include_subfolders.get()):
                        result = self.standardizer.rename_file(path, dry_run=True, reserved=reserved)
                        if result["status"] == "would_rename":
                            rows.append((result["old_name"], result["new_name"], "filename-only"))
                else:
                    renamer = ContextualRenamer(index_path=self.index_path.get() if os.path.exists(self.index_path.get()) else None)
                    for path in iter_files(target, self.include_subfolders.get()):
                        result = renamer.rename_file(path, dry_run=True, force=False, reserved=reserved)
                        if result["status"] == "would_rename":
                            rows.append((result["old_name"], result["new_name"], "context-aware"))
                self.results_queue.put(("preview", {"rows": rows or [("No changes proposed", "", "")], "message": f"Preview ready: {len(rows)} proposed rename(s)"}))
            except Exception as exc:
                self.results_queue.put(("error", str(exc)))

        self._run_async(worker, "Preparing rename preview...")

    def _rename_apply(self):
        target = self._require_target()
        if not target:
            return
        if not messagebox.askyesno(APP_TITLE, f"Apply {self.rename_mode.get().lower()} rename to:\n{target}\n\nScope: {'Include subfolders' if self.include_subfolders.get() else 'This folder only'}"):
            return
        self.mode_label.set("Apply changes")
        self._refresh_target_summary()

        def worker():
            try:
                rows = []
                reserved = set()
                if self.rename_mode.get() == "Quick":
                    for path in iter_files(target, self.include_subfolders.get()):
                        result = self.standardizer.rename_file(path, dry_run=False, reserved=reserved)
                        if result["status"] == "renamed":
                            rows.append((result["old_name"], result["new_name"], "renamed"))
                else:
                    renamer = ContextualRenamer(index_path=self.index_path.get() if os.path.exists(self.index_path.get()) else None)
                    for path in list(iter_files(target, self.include_subfolders.get())):
                        result = renamer.rename_file(path, dry_run=False, force=False, reserved=reserved)
                        if result["status"] == "renamed":
                            rows.append((result["old_name"], result["new_name"], "renamed"))
                self.results_queue.put(("preview", {"rows": rows or [("No files renamed", "", "")], "message": f"Rename complete: {len(rows)} file(s) changed"}))
                self.results_queue.put(("done", "Rename complete"))
                self.mode_label.set("Preview only")
                self.after(0, self._refresh_target_summary)
                self.after(0, self._estimate_target_stats)
            except Exception as exc:
                self.results_queue.put(("error", str(exc)))

        self._run_async(worker, "Applying rename...")

    def _load_index_manager(self):
        return IndexManager(self._resolve_index_path_for_runtime())

    def _create_plan(self):
        target = self._require_target()
        if not target:
            return
        self.mode_label.set("Preview only")
        self._refresh_target_summary()

        def worker():
            try:
                session_name = safe_session_name(target)
                session_dir = PROJECT_DIR / "runs" / session_name
                session_dir.mkdir(parents=True, exist_ok=True)
                out_csv = session_dir / "drive_scan.csv"
                files = 0
                with open(out_csv, "w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["path", "name", "type"])
                    for path in iter_files(target, self.include_subfolders.get()):
                        rel_path = os.path.relpath(path, target)
                        name, ext = os.path.splitext(os.path.basename(path))
                        writer.writerow([rel_path, name, ext.lower()])
                        files += 1

                subprocess.run(
                    [sys.executable, "-m", "johnny.scan.drive_context_analyzer", str(session_dir)],
                    check=True,
                    cwd=str(PROJECT_DIR),
                    capture_output=True,
                    text=True,
                )

                handoff_path = session_dir / "llm_master_index_handoff.json"
                if not handoff_path.exists():
                    raise FileNotFoundError(f"Expected handoff file was not created: {handoff_path}")

                rows = [
                    ("Files scanned", str(files), "ready"),
                    ("Evidence package", "prepared", "internal"),
                    ("JD handoff", "ready", "LLM input"),
                    ("Next step", "generate index", "review with LLM"),
                ]
                self.results_queue.put((
                    "plan_ready",
                    {
                        "rows": rows,
                        "reason_header": "State",
                        "open_path": str(handoff_path),
                    },
                ))
            except subprocess.CalledProcessError as exc:
                detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
                self.results_queue.put(("error", detail))
            except Exception as exc:
                self.results_queue.put(("error", str(exc)))

        self._run_async(worker, "Creating JD plan artifacts...")

    def _build_organization_plan(self, idx, target):
        categories = list(idx.data.get("categories", {}).keys())
        nlp = NLPEngine(categories, category_profiles=idx.profiles)
        file_paths = list(iter_files(target, self.include_subfolders.get()))
        # Preview and Apply both build this plan with the same settings, so they always agree (source == preview == moved still holds).
        # Big jobs cache the file texts' embeddings: only the buckets change between rounds, so a re-check takes seconds.
        def cache_for():
            session_dir = PROJECT_DIR / "runs" / safe_session_name(target)
            session_dir.mkdir(parents=True, exist_ok=True)
            return str(session_dir / "file_embeddings.npz")

        # One shared function plans everything; the 1/150 guard (_index_preview_failed) counts Unsorted after it.
        planned_items, grouped, stats = sorting.plan_organization(idx, nlp, target, file_paths, self.sort_settings, cache_for=cache_for)
        self.sort_stats = stats
        self.folder_vote_moved = stats["voted"]

        summary_rows = [(folder, str(count), "planned file(s)") for folder, count in grouped.most_common()]
        return file_paths, planned_items, summary_rows

    def _open_sort_settings(self):
        open_sort_settings(self, self.sort_settings, self._apply_sort_settings)

    def _apply_sort_settings(self, settings):
        self.sort_settings = settings
        self.status_text.set("Settings changed. Run Preview Organization to see the effect.")

    def _write_leftover_handoff(self, idx, target, planned_items, rows):
        """After a failed 1/150 check: the files left in Unsorted, grouped, with the closest buckets, for the next LLM round."""
        unsorted = [os.path.relpath(i["path"], target) for i in planned_items if i["topic"] == "Unsorted_Miscellaneous"]
        needing = sum(int(r[1]) for r in rows if str(r[0]) not in SPECIAL_DIRS)
        nlp = NLPEngine(list(idx.data["categories"]), category_profiles=idx.profiles)

        def nearest(sample_paths):
            return summarize_closest([nlp.get_category_candidates(plan_text(os.path.join(target, rel)), top_k=1) for rel in sample_paths])

        session_dir = PROJECT_DIR / "runs" / safe_session_name(target)
        session_dir.mkdir(parents=True, exist_ok=True)
        out = session_dir / "llm_leftovers_handoff.json"
        write_leftover_handoff_json(out, session_dir.name, idx.data, group_leftovers(unsorted, nearest), len(unsorted), needing // 150, needing)
        return out

    def _get_index_failure_alert(self, total_files: int, rows) -> str:
        if self._index_preview_failed(total_files, rows):
            return "! Index failed"
        return ""

    def _organize_preview(self):
        target = self._require_target()
        if not target:
            return
        self.mode_label.set("Preview only")
        self._refresh_target_summary()

        def worker():
            try:
                idx = self._load_index_manager()
                if self.organize_mode.get() == "Category Plan":
                    rows = [(code, topic, "category") for topic, code in idx.data.get("categories", {}).items()]
                    self.results_queue.put(("preview", {"rows": rows, "reason_header": "Type", "message": f"Loaded {len(rows)} JD categories", "open_path": self.index_path.get()}))
                    return

                file_paths, planned_items, rows = self._build_organization_plan(idx, target)
                planned_total = sum(int(row[1]) for row in rows)
                if planned_total != len(file_paths):
                    raise ValueError(f"Sanity check failed during preview: source files={len(file_paths)}, preview total={planned_total}")
                alert = self._get_index_failure_alert(planned_total, rows)
                leftovers = self._write_leftover_handoff(idx, target, planned_items, rows) if alert else None
                self.results_queue.put((
                    "preview",
                    {
                        "rows": rows or [("No files found", "0", "planned file(s)")],
                        "reason_header": "Status",
                        "message": f"Preview ready: {planned_total} file(s) across {len(rows)} JD folder(s)"
                        + (f"; {self.folder_vote_moved} followed their folder" if self.folder_vote_moved else "")
                        + f"; left for the next pass: {self.sort_stats['unsorted']} (limit {self.sort_stats['allowed']})"
                        + f". Settings used: lead {self.sort_settings.lead:.3f}, vote {'off' if not self.sort_settings.vote else f'{self.sort_settings.vote_share:.0%}'}"
                        + (f". Leftover file for the next LLM round: {leftovers}" if leftovers else ""),
                        "alert_message": alert,
                        "open_path": str(leftovers) if leftovers else None,
                    },
                ))
            except Exception as exc:
                self.results_queue.put(("error", str(exc)))

        self._run_async(worker, "Preparing organization preview...")

    def _organize_apply(self):
        target = self._require_target()
        if not target:
            return
        if self.organize_mode.get() != "Apply Organization":
            messagebox.showinfo(APP_TITLE, "Switch to 'Apply Organization' before running this action.")
            return
        dest = default_sorted_root()
        while True:
            answer = messagebox.askyesnocancel(
                APP_TITLE,
                f"Apply organization to:\n{target}\n\nSorted files will be moved into:\n{dest}\n\nYes = go ahead   No = choose another folder   Cancel = stop",
            )
            if answer is None:
                return
            if answer:
                break
            chosen = filedialog.askdirectory(title="Choose where the sorted folders go", initialdir=os.path.dirname(dest) or None)
            if chosen:
                dest = os.path.abspath(chosen)
        self.mode_label.set("Apply changes")
        self._refresh_target_summary()

        def worker():
            try:
                idx = self._load_index_manager()
                file_paths, planned_items, preview_rows = self._build_organization_plan(idx, target)
                source_total = len(file_paths)
                preview_total = sum(item["count"] for item in planned_items)
                if source_total != preview_total:
                    raise ValueError(f"Sanity check failed before apply: source files={source_total}, preview total={preview_total}")
                if self._index_preview_failed(preview_total, preview_rows):
                    raise ValueError("Apply blocked: index failed the 1/150 unsorted rule.")

                router = Router(idx, dest)
                moved_counts = Counter()
                moved_total = 0
                for item in planned_items:
                    if item["topic"] is None:
                        moved_path = router.route_special(item["path"], item["destination"], item["rel"])
                    else:
                        moved_path = router.route_file(item["path"], item["topic"])
                    if moved_path and os.path.exists(moved_path):
                        moved_total += item["count"]
                        moved_counts[item["destination"]] += item["count"]

                if source_total != moved_total:
                    raise ValueError(
                        f"Sanity check failed after apply: source files={source_total}, preview total={preview_total}, moved files={moved_total}"
                    )

                rows = [(folder, str(count), "moved file(s)") for folder, count in moved_counts.most_common()]
                self.results_queue.put((
                    "preview",
                    {
                        "rows": rows or [("No files moved", "0", "moved file(s)")],
                        "reason_header": "Status",
                        "message": f"Organization complete: source={source_total}, preview={preview_total}, moved={moved_total}",
                        "alert_message": self._get_index_failure_alert(moved_total, rows),
                        "open_path": dest,
                    },
                ))
                self.results_queue.put(("done", "Organization complete"))
                self.mode_label.set("Preview only")
                self.after(0, self._refresh_target_summary)
                self.after(0, self._estimate_target_stats)
                self.after(0, lambda: self._notify_sorted(moved_total, dest))
            except Exception as exc:
                self.results_queue.put(("error", str(exc)))

        self._run_async(worker, "Applying organization...")

    def _notify_sorted(self, moved_total, dest):
        messagebox.showinfo(APP_TITLE, f"Sorted {moved_total} file(s) into:\n{dest}\n\nUse the Open button to see the folder. Every move is logged in undo_log.jsonl.")


def main():
    app = PrototypeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
