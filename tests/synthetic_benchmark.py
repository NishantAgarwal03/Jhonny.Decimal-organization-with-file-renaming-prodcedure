"""On-demand judge for the bucket loop on the synthetic drive (loads the model; not part of the unittest suite).

    python tests/synthetic_benchmark.py                          # round 1 index, three settings, per-situation table
    python tests/synthetic_benchmark.py --index index_round2.json
    python tests/synthetic_benchmark.py --update-baseline        # accept the current default-setting numbers
    python tests/make_synthetic_drive.py                         # regenerate the scan and the answer key first if needed

The answer key (fixtures/synthetic/synthetic_truth.csv) is the same files fully sorted as they should be. For every file
the tool sorted it is scored as:
    correct   the right bucket (or Unsorted for a file that holds no usable information)
    wrong     a wrong bucket: the costly mistake, the project rule is that a wrong folder is worse than an unsorted file
    missed    left in Unsorted although a right bucket exists
Nothing is moved; only the scan CSV and the index are read. Exit code 1 if correct drops or wrong rises vs the baseline.
"""
import argparse
import collections
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from johnny.core.sorting import presets
from johnny.tools import loop_check

SYN = os.path.join(ROOT, "fixtures", "synthetic")
SCAN = os.path.join(SYN, "synthetic_scan.csv")
TRUTH = os.path.join(SYN, "synthetic_truth.csv")
BUCKET_MAP = os.path.join(SYN, "index_bucket_map.json")
BASELINE = os.path.join(SYN, "baseline.json")
OUT = os.path.join(ROOT, "loop_check_out", "synthetic")
UNS = "Unsorted_Miscellaneous"
RULE_BUCKETS = {"_Software_Projects", "_Temp_Safe_To_Remove"}
# A fourth setting, "vote only if the file's own bucket matches the folder's", was tried and dropped (see planner.folder_vote for the figures).
# Rows: the settings used before the file type and the safeguards existed, then the two presets the UI offers (the second pass is the default).
SETTINGS = [("old: lead 0.02, no vote, no safeguards", dict(lead=0.02, vote=False, no_info_rule=False, vote_type_check=False)),
            ("first pass preset (strict)", dict(settings=presets()["first"])),
            ("second pass preset (normal, default)", dict(settings=presets()["second"]))]
DEFAULT = SETTINGS[-1][0]


def load_truth(path=TRUTH):
    with open(path, encoding="utf-8", newline="") as f:
        return {r["path"]: (r["bucket"], r["tag"]) for r in csv.DictReader(f)}


def score(assignments, truth, bucket_map):
    """-> {"all": counts, "<tag>": counts, "rule_failures": n}; counts = n, correct, wrong, missed, unsorted."""
    out = collections.defaultdict(lambda: collections.Counter())
    rule_failures = 0
    for path, bucket, how in assignments:
        expected, tag = truth[path]
        if expected in RULE_BUCKETS:
            rule_failures += 1              # the fixed rules should have taken this file out before any bucket was chosen
            continue
        got = bucket_map.get(bucket, bucket)
        verdict = "correct" if got == expected else "missed" if got == UNS else "wrong"
        for key in ("all", tag):
            out[key]["n"] += 1
            out[key][verdict] += 1
            out[key]["unsorted"] += got == UNS
    result = {k: dict(v) for k, v in out.items()}
    result["rule_failures"] = rule_failures
    return result


def pct(part, whole): return f"{100 * part / max(1, whole):5.1f}%"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index", default="index_round1.json", help="index file name inside fixtures/synthetic")
    parser.add_argument("--data", default=SYN, help="folder holding synthetic_scan.csv and synthetic_truth.csv (e.g. a hold-out made with another seed)")
    parser.add_argument("--model", default=None, help="Hugging Face model id to test instead of the default (its own cache folder; the baseline is not touched)")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--tolerance", type=float, default=0.0, help="allowed drop in correct / rise in wrong, in percentage points")
    args = parser.parse_args()

    scan = os.path.join(args.data, "synthetic_scan.csv")
    out = OUT if not args.model else os.path.join(OUT, args.model.replace("/", "__"))
    truth = load_truth(os.path.join(args.data, "synthetic_truth.csv"))
    with open(BUCKET_MAP, encoding="utf-8") as f:
        bucket_map = json.load(f)
    index = os.path.join(SYN, args.index)
    oracle_unsorted = sum(1 for _, (b, _) in truth.items() if b == UNS)
    results = {}
    print(f"Index: {args.index}\n")
    print(f"{'setting':36} {'correct':>8} {'WRONG':>8} {'missed':>8} {'Unsorted':>9} {'guard':>16}")
    for name, kw in SETTINGS:
        s = loop_check.check(scan, index, out, model_name=args.model, **kw)
        sc = score(s["assignments"], truth, bucket_map)
        results[name] = sc
        a = sc["all"]
        print(f"{name:36} {pct(a.get('correct', 0), a['n']):>8} {pct(a.get('wrong', 0), a['n']):>8} {pct(a.get('missed', 0), a['n']):>8} {s['unsorted']:>9} "
              f"{('PASS' if s['passed'] else 'FAIL') + f' (<= {s['allowed']})':>16}")
        if sc["rule_failures"]:
            print(f"   !! {sc['rule_failures']} files the fixed rules should have taken out reached the buckets")
    print(f"\nThe answer key itself leaves {oracle_unsorted} files Unsorted (names with no usable information); "
          f"a perfect sort would therefore {'pass' if oracle_unsorted <= s['allowed'] else 'FAIL'} the 1-in-150 check ({oracle_unsorted} vs {s['allowed']} allowed).")

    default = results[DEFAULT]
    print(f"\nPer situation, {DEFAULT}:")
    print(f"   {'situation':22} {'files':>6} {'correct':>8} {'WRONG':>8} {'missed':>8}")
    for tag, c in sorted(((k, v) for k, v in default.items() if k not in ("all", "rule_failures")), key=lambda kv: -kv[1]["n"]):
        print(f"   {tag:22} {c['n']:6d} {pct(c.get('correct', 0), c['n']):>8} {pct(c.get('wrong', 0), c['n']):>8} {pct(c.get('missed', 0), c['n']):>8}")

    if args.model:
        return                      # a model comparison is for reading, not for the regression baseline
    pct1 = lambda r, k: round(100 * r["all"].get(k, 0) / r["all"]["n"], 1)
    now = {"second": {"correct": pct1(default, "correct"), "wrong": pct1(default, "wrong")},
           "first": {"correct": pct1(results[SETTINGS[1][0]], "correct"), "wrong": pct1(results[SETTINGS[1][0]], "wrong")}}
    baseline = json.load(open(BASELINE, encoding="utf-8")) if os.path.exists(BASELINE) else {}
    if args.update_baseline:
        baseline[args.index] = now
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(baseline, f, indent=2)
        print(f"\nBaseline for {args.index} written: {now}")
        return
    was = baseline.get(args.index)
    if was and "second" in was:
        bad = [f"{p}: correct {was[p]['correct']} -> {now[p]['correct']}, wrong {was[p]['wrong']} -> {now[p]['wrong']}" for p in ("first", "second")
               if now[p]["correct"] < was[p]["correct"] - args.tolerance or now[p]["wrong"] > was[p]["wrong"] + args.tolerance]
        if bad:
            sys.exit("\nSYNTHETIC REGRESSION:\n  " + "\n  ".join(bad))
        print(f"\nOK: no regression against the baseline {was} (now {now}).")


if __name__ == "__main__":
    main()
