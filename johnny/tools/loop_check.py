"""Run the bucket loop from a scan CSV alone. Nothing is moved and nothing is written next to the drive.

    python -m johnny.tools.loop_check drive_scan.csv --round1 [--note author_note.txt] [--out DIR]
        writes DIR/llm_master_index_handoff.json: give it to the LLM and save the reply as an index .json
    python -m johnny.tools.loop_check drive_scan.csv master_index.json [--out DIR]
        sorts every file with the local model, prints Unsorted against the 1-in-150 limit and, when it
        fails, writes DIR/llm_leftovers_handoff.json for the next LLM round (exit code 2)

File embeddings are cached in DIR, so only the first check on a CSV is slow.
"""
import argparse
import csv
import os
import sys
from collections import Counter
from dataclasses import replace

from johnny.core.evidence_engine import group_leftovers, summarize_closest, write_leftover_handoff_json, write_llm_handoff_json
from johnny.core.index_manager import IndexManager
from johnny.core.nlp_engine import NLPEngine
from johnny.core.planner import split_files
from johnny.core.sorting import plan_text, presets, sort_files

UNSORTED = "Unsorted_Miscellaneous"


def strip_scan_root(paths):
    """Some scans store the scan root in every path (Users/Admin/Downloads/...). Drop the folders shared by all files,
    so the scan root is the top and no vote can happen "above" it. A scan made by the UI has nothing to strip."""
    parts = [p.replace("/", os.sep).split(os.sep) for p in paths]
    common = 0
    while all(len(x) > common + 1 for x in parts) and len({x[common] for x in parts}) == 1:
        common += 1
    return [os.sep.join(x[common:]) for x in parts]


def read_rows(csv_path):
    with open(csv_path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_round1(csv_path, out_dir, note_path=None):
    rows = read_rows(csv_path)
    note = open(note_path, encoding="utf-8").read().strip() if note_path else ""
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "llm_master_index_handoff.json")
    write_llm_handoff_json(out, os.path.basename(os.path.dirname(os.path.abspath(csv_path))), [], [], {}, len(rows), scan_rows=rows, author_note=note)
    return out


def check(csv_path, index_path, out_dir, settings=None, model_name=None, types="ext", **override):
    """-> {needing, unsorted, allowed, passed, counts, voted, handoff, assignments, stats}. Same rules and decisions as the UI's Apply Organization preview.
    settings = sorting.SortSettings (default: the second-pass preset). override = any SortSettings field by name (lead=0.02, vote=False, vote_share=0.9 ...)
    so a benchmark can compare settings; model_name = a Hugging Face id and types = None | "ext" | "kind" (the type word the model reads) likewise (use a
    separate out_dir per model: the embedding cache is per model). assignments = [(path, bucket, "model" | "vote" | "unsorted")]."""
    settings = replace(settings or presets()["second"], **override)
    paths = strip_scan_root([r["path"] for r in read_rows(csv_path)])
    units, junk, routed = split_files(".", paths)
    idx = IndexManager(index_path)
    nlp = NLPEngine(list(idx.data["categories"]), category_profiles=idx.profiles, model_name=model_name)
    os.makedirs(out_dir, exist_ok=True)
    sorted_ = sort_files(nlp, routed, [plan_text(p, types) for p in routed], settings, cache_path=os.path.join(out_dir, "file_embeddings.npz"))
    topics = [t if idx.get_category_prefix(t) else UNSORTED for t in sorted_["topics"]]
    counts = Counter(topics)
    needing, unsorted = len(routed), counts[UNSORTED]
    allowed = needing // 150
    summary = {"files": len(paths), "units": len(units), "unit_files": sum(units.values()), "junk": len(junk), "needing": needing,
               "unsorted": unsorted, "allowed": allowed, "passed": unsorted <= allowed, "counts": counts, "voted": sorted_["stats"]["voted"],
               "stats": sorted_["stats"], "handoff": None,
               "assignments": [(p, t, "unsorted" if how in ("unsorted", "held") else how) for p, t, how in zip(routed, topics, sorted_["how"])]}
    if not summary["passed"]:
        candidates = {p: c[:1] for p, c in zip(routed, sorted_["candidates"])}
        left = [p for p, t in zip(routed, topics) if t == UNSORTED]
        groups = group_leftovers(left, nearest=lambda samples: summarize_closest([candidates[p] for p in samples]))
        summary["handoff"] = os.path.join(out_dir, "llm_leftovers_handoff.json")
        write_leftover_handoff_json(summary["handoff"], os.path.basename(os.path.dirname(os.path.abspath(csv_path))), idx.data, groups, unsorted, allowed, needing)
        summary["worst_groups"] = [(g["folder"], g["files"]) for g in groups[:5]]
    return summary


def main():
    sys.stdout.reconfigure(errors="replace")  # folder names can hold characters the Windows console cannot print
    parser = argparse.ArgumentParser(description="Run the bucket loop's check from a scan CSV.")
    parser.add_argument("csv", help="drive_scan.csv (path, name, type); only read")
    parser.add_argument("index", nargs="?", help="master_index.json written by the LLM")
    parser.add_argument("--round1", action="store_true", help="write the first handoff for the LLM instead of checking an index")
    parser.add_argument("--note", help="text file about what the drive is for (used with --round1)")
    parser.add_argument("--out", default="loop_check_out", help="where handoff files and the embedding cache go")
    parser.add_argument("--pass", dest="which_pass", choices=["1", "2"], default="2", help="1 = strict first pass (no wrong folders is the aim), 2 = normal second pass (default)")
    args = parser.parse_args()

    if args.round1:
        print(f"Round 1 handoff: {write_round1(args.csv, args.out, args.note)}")
        return
    if not args.index:
        parser.error("give an index file, or use --round1")
    s = check(args.csv, args.index, args.out, settings=presets()["first" if args.which_pass == "1" else "second"])
    print(f"files {s['files']} | software folders {s['units']} ({s['unit_files']} files) | junk {s['junk']} | need a bucket {s['needing']}")
    for name, count in s["counts"].most_common(15):
        print(f"  {count:7d}  {name}")
    st = s["stats"]
    print(f"{st['voted']} followed their folder (folder vote) | {st['held_no_information']} held, no usable information | {st['blocked_by_type']} held by the type check")
    print(f"Unsorted {s['unsorted']} of {s['needing']}; allowed {s['allowed']} (1 in 150) -> {'PASS' if s['passed'] else 'FAIL'}")
    if not s["passed"]:
        print("Biggest leftover groups:", s["worst_groups"])
        print(f"Next LLM round: {s['handoff']}")
        sys.exit(2)


if __name__ == "__main__":
    main()
