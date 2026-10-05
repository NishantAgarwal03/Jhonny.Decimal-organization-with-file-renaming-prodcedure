"""The one place that decides which bucket each file goes to, shared by the UI and tools/loop_check.

Order: the local model proposes a bucket per file -> files whose path says nothing are held back (safeguard A) -> files the
model refused may follow their folder (the folder vote, with safeguard B, the type check). Safeguards A and B are always on:
they only ever hold files back for the next pass, they never move a file anywhere.
"""
import os
from collections import Counter
from dataclasses import dataclass, replace

from johnny.core.embedding_manager import load_routing_calibration
from johnny.core.planner import REVIEW_DIR, SAFE_DIR, SOFTWARE_DIR, folder_vote, has_information, relative_parent, split_files
from johnny.core.utils import sanitize_topic_name

UNSORTED = "Unsorted_Miscellaneous"


@dataclass(frozen=True)
class SortSettings:
    lead: float = 0.01            # how far ahead the best bucket must be of the second-best
    vote: bool = True             # refused files may follow their folder
    vote_share: float = 0.8       # how much of a folder must agree before its refused files follow
    min_files: int = 20           # smallest folder allowed to vote
    no_info_rule: bool = True     # safeguard A (not shown in the UI)
    vote_type_check: bool = True  # safeguard B (not shown in the UI)


# Ranges the UI offers and the code enforces. Outside them the numbers stop meaning anything useful.
LIMITS = {"lead": (0.0, 0.05), "vote_share": (0.5, 1.0), "min_files": (5, 200)}


def clamp(settings):
    """Settings with every number pulled inside LIMITS."""
    fix = lambda name, value: min(max(value, LIMITS[name][0]), LIMITS[name][1])
    return replace(settings, lead=fix("lead", settings.lead), vote_share=fix("vote_share", settings.vote_share), min_files=int(fix("min_files", settings.min_files)))


def presets(path=None):
    """{"first": strict, "second": normal}. The second pass uses the lead in routing_calibration.json (one source of truth);
    the first pass overrides come from its optional "passes" -> "first" entry, else the defaults below."""
    import json
    from pathlib import Path
    from johnny.core.embedding_manager import PROJECT_DIR
    second = SortSettings(lead=load_routing_calibration(path)["top1_top2_margin_threshold"])
    first = replace(second, lead=0.03, vote_share=0.9)
    file = Path(path) if path else PROJECT_DIR / "routing_calibration.json"
    if file.exists():
        with open(file, encoding="utf-8") as handle:
            extra = (json.load(handle).get("passes") or {}).get("first") or {}
        first = replace(first, **{k: v for k, v in extra.items() if k in SortSettings.__dataclass_fields__})
    return {"first": clamp(first), "second": clamp(second)}


def sort_files(nlp, rel_paths, texts, settings, cache_path=None):
    """-> {"topics", "how", "candidates", "stats"}. rel_paths are relative to the scanned folder; texts is what the model reads.
    how[i] is "model", "vote", "held" (no information) or "unsorted". stats counts each, plus files the type check held back."""
    settings = clamp(settings)
    if hasattr(nlp, "routing_calibration"):
        nlp.routing_calibration = dict(nlp.routing_calibration, top1_top2_margin_threshold=settings.lead)
    results = nlp.get_topic_details_many(texts, cache_path=cache_path)
    held = [settings.no_info_rule and not has_information(p) for p in rel_paths]
    own = [UNSORTED if h else r[0] for r, h in zip(results, held)]
    closest = [r[2][0]["category"] if r[2] and not h else UNSORTED for r, h in zip(results, held)]
    stats = {}
    topics, voted = (folder_vote(rel_paths, own, closest, share=settings.vote_share, min_files=settings.min_files, hold=held,
                                 type_check=settings.vote_type_check, stats=stats) if settings.vote else (own, 0))
    how = ["held" if h else "model" if o != UNSORTED else "vote" if t != UNSORTED else "unsorted" for h, o, t in zip(held, own, topics)]
    stats.update(voted=voted, held_no_information=sum(held), blocked_by_type=stats.get("blocked_by_type", 0), unsorted=sum(t == UNSORTED for t in topics))
    return {"topics": topics, "how": how, "candidates": [r[2] for r in results], "stats": stats}


# General file kinds (not drive-specific) so the model can tell a document from a photo or a program.
FILE_KINDS = {
    "image": "jpg jpeg png gif bmp webp tif tiff heic cr2 raw", "video": "mp4 mkv avi mov wmv flv m4v webm ts",
    "audio": "mp3 wav flac aac m4a ogg wma m4b au", "document": "pdf doc docx txt rtf odt", "spreadsheet": "xls xlsx xlsm csv ods",
    "presentation": "ppt pptx", "ebook": "epub mobi djvu azw3", "subtitle": "srt sub vtt",
    "code": "py js java c cpp cs html css ipynb sql json xml yml", "program": "exe msi dll", "archive": "zip rar 7z tar gz iso",
    "data": "dat db bin pak",
}
KIND_OF = {ext: kind for kind, exts in FILE_KINDS.items() for ext in exts.split()}


def plan_text(path: str, types="ext") -> str:
    """The text the UI routes on: filename + folder path, separators flattened.
    types="ext" (default) appends the extension word (docx): without it a .docx and a .jpg with similar names look identical to the model.
    types=None is the old text; "kind" appends extension and kind (docx document). On the synthetic judge "ext" gave the fewest wrong folders."""
    base, ext = os.path.splitext(os.path.basename(path))
    clean_root = os.path.dirname(path).replace("\\", " ").replace("/", " ").replace("_", " ").replace("-", " ")
    text = f"{base.replace('_', ' ').replace('-', ' ')} {clean_root}"
    ext = ext.lower().lstrip(".")
    if types and ext:
        text += f" {ext}" + (f" {KIND_OF[ext]}" if types == "kind" and ext in KIND_OF else "")
    return text


def plan_organization(idx, nlp, target, file_paths, settings, text_of=plan_text, cache_for=None):
    """The one plan for the UI and the CLI: -> (planned_items, grouped Counter, stats).
    Software units and junk go by rule; every other file is sorted by sort_files and gets its folder name from the index.
    cache_for is called only for big jobs (more than 2000 routed files) and returns the embedding cache path."""
    units, junk, routed = split_files(target, file_paths)
    planned_items, grouped = [], Counter()
    for unit, count in units.items():
        planned_items.append({"path": unit, "topic": None, "destination": SOFTWARE_DIR, "count": count, "rel": relative_parent(target, unit)})
        grouped[SOFTWARE_DIR] += count
    for path, tier in junk.items():
        destination = SAFE_DIR if tier == "safe" else REVIEW_DIR
        planned_items.append({"path": path, "topic": None, "destination": destination, "count": 1, "rel": relative_parent(target, path)})
        grouped[destination] += 1
    cache = cache_for() if cache_for and len(routed) > 2000 else None
    result = sort_files(nlp, [os.path.relpath(p, target) for p in routed], [text_of(p) for p in routed], settings, cache_path=cache)
    for path, topic in zip(routed, result["topics"]):
        code = idx.get_category_prefix(topic)
        if not code:
            topic = UNSORTED
            code = idx.get_category_prefix(topic) or "80.01"
        destination_folder = f"{code}_{sanitize_topic_name(topic)}"
        planned_items.append({"path": path, "topic": topic, "destination": destination_folder, "count": 1})
        grouped[destination_folder] += 1
    return planned_items, grouped, dict(result["stats"], needing=len(routed), allowed=len(routed) // 150)
