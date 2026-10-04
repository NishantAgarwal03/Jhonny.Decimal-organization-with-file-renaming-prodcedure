"""On-demand routing benchmark: accuracy, abstention rate and misfile rate, compared with a stored baseline.

Run from the repo root (loads the embedding model, so it is not part of the unittest suite):
    python tests/routing_benchmark.py                   # compare with fixtures/routing_baseline.json
    python tests/routing_benchmark.py --update-baseline # accept the current numbers as the new baseline

Read-only: never calls jd_benchmark.tune_calibration, so routing_calibration.json is not touched.
Two modes: "full" routes on name + path + extension + text excerpt (organizer.py); "ui" routes on
name + path only, via jd_ui_prototype.plan_text (what the UI does).
Exit code 1 if accuracy drops or the misfile rate (wrong, non-Unsorted) rises beyond --tolerance.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from johnny.core.embedding_manager import DEFAULT_MODEL_NAME, load_category_profiles
from johnny.tools.jd_benchmark import build_case_text, load_categories, load_fixture
from johnny.ui.jd_ui_prototype import plan_text
from johnny.core.nlp_engine import NLPEngine

FIXTURE = os.path.join(ROOT, "fixtures", "jd_benchmark_cases.jsonl")
INDEX = os.path.join(ROOT, "fixtures", "jd_benchmark_index.json")
PROFILES = os.path.join(ROOT, "fixtures", "jd_benchmark_profiles.json")  # category_profiles.json holds none of these categories
BASELINE = os.path.join(ROOT, "fixtures", "routing_baseline.json")
UNSORTED = "Unsorted_Miscellaneous"


def case_text(case, mode):
    if mode == "ui":
        return plan_text(os.path.join(case.get("parent_path", ""), case["current_name"]))
    return build_case_text(case)


def evaluate(engine, rows, mode):
    correct = abstained = misfiled = 0
    for case in rows:
        predicted = engine.get_topic(case_text(case, mode))
        expected = case["expected_category"]
        correct += predicted == expected
        abstained += predicted == UNSORTED
        misfiled += predicted != expected and predicted != UNSORTED
    n = len(rows)
    return {"accuracy": round(correct / n, 4), "abstention_rate": round(abstained / n, 4), "misfile_rate": round(misfiled / n, 4), "cases": n}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--tolerance", type=float, default=0.0, help="Allowed drop in accuracy / rise in misfile rate (default 0).")
    args = parser.parse_args()

    rows = load_fixture(FIXTURE)
    engine = NLPEngine(categories=load_categories(INDEX), category_profiles=load_category_profiles(PROFILES), model_name=DEFAULT_MODEL_NAME)
    if engine.model is None:
        sys.exit("Embedding model unavailable; cannot benchmark routing.")
    current = {mode: evaluate(engine, rows, mode) for mode in ("full", "ui")}
    print(json.dumps(current, indent=2))

    if args.update_baseline:
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        print(f"Baseline written to {BASELINE}")
        return
    if not os.path.exists(BASELINE):
        sys.exit("No baseline yet. Run with --update-baseline once and review the numbers.")
    with open(BASELINE, encoding="utf-8") as f:
        baseline = json.load(f)
    failures = []
    for mode, now in current.items():
        was = baseline[mode]
        if now["accuracy"] < was["accuracy"] - args.tolerance:
            failures.append(f"{mode}: accuracy {was['accuracy']} -> {now['accuracy']}")
        if now["misfile_rate"] > was["misfile_rate"] + args.tolerance:
            failures.append(f"{mode}: misfile_rate {was['misfile_rate']} -> {now['misfile_rate']}")
    if failures:
        sys.exit("ROUTING REGRESSION:\n  " + "\n  ".join(failures))
    print("OK: no routing regression against baseline.")


if __name__ == "__main__":
    main()
