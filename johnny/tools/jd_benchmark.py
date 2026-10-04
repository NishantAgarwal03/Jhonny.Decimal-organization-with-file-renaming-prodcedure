import json
from collections import Counter
from pathlib import Path

from johnny.core.embedding_manager import load_category_profiles
from johnny.core.nlp_engine import NLPEngine


PROJECT_DIR = Path(__file__).resolve().parents[2]  # repo root


def load_fixture(fixture_path):
    rows = []
    with open(fixture_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def load_categories(index_path):
    with open(index_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return list(data.get("categories", {}).keys())


def build_case_text(case):
    parts = [
        case.get("current_name", ""),
        case.get("parent_path", ""),
        case.get("extension", ""),
        case.get("text_excerpt", ""),
    ]
    return " ".join(part for part in parts if part).strip()


def compute_metrics(rows, predictions):
    total = len(rows)
    correct = 0
    top2_hits = 0
    unsorted = 0
    confusion = Counter()
    coarse_correct = 0

    for case, result in zip(rows, predictions):
        expected = case["expected_category"]
        predicted = result["predicted"]
        if predicted == expected:
            correct += 1
        if expected in result["top2"]:
            top2_hits += 1
        if predicted == "Unsorted_Miscellaneous":
            unsorted += 1
        if predicted != expected:
            confusion[(expected, predicted)] += 1
        if case.get("expected_coarse_group") == result.get("coarse_group"):
            coarse_correct += 1

    return {
        "accuracy": round(correct / total, 4) if total else 0.0,
        "unsorted_ratio": round(unsorted / total, 4) if total else 0.0,
        "top_2_recall": round(top2_hits / total, 4) if total else 0.0,
        "coarse_accuracy": round(coarse_correct / total, 4) if total else 0.0,
        "biggest_confusion_pairs": [
            {"expected": expected, "predicted": predicted, "count": count}
            for (expected, predicted), count in confusion.most_common(10)
        ],
    }


def infer_coarse_group(category):
    mapping = {
        "Identity_Documents": "identity",
        "Legal_Case_Files": "legal",
        "Financial_Receipts_Bills": "receipts",
        "Study_Notes_Exam_Prep": "study",
        "Unsorted_Miscellaneous": "mixed_weak",
    }
    return mapping.get(category, "other")


def tune_calibration(rows, engine, output_path):
    best = None
    for dense_threshold in [0.18, 0.22, 0.26, 0.30, 0.34]:
        for margin_threshold in [0.02, 0.04, 0.06, 0.08]:
            for unsorted_override in [0.12, 0.16, 0.20, 0.24]:
                engine.routing_calibration = {
                    "dense_confidence_threshold": dense_threshold,
                    "top1_top2_margin_threshold": margin_threshold,
                    "unsorted_override_threshold": unsorted_override,
                }
                predictions = []
                for case in rows:
                    text = build_case_text(case)
                    predicted, score, _ = engine.get_topic_details(text)
                    candidates = engine.get_category_candidates(text, top_k=2)
                    predictions.append(
                        {
                            "predicted": predicted,
                            "score": score,
                            "top2": [item["category"] for item in candidates],
                            "coarse_group": infer_coarse_group(predicted),
                        }
                    )
                metrics = compute_metrics(rows, predictions)
                objective = metrics["accuracy"] - metrics["unsorted_ratio"] * 0.5 + metrics["top_2_recall"] * 0.25
                candidate = (objective, metrics, dict(engine.routing_calibration))
                if best is None or candidate[0] > best[0]:
                    best = candidate

    Path(output_path).write_text(json.dumps(best[2], indent=2), encoding="utf-8")
    engine.routing_calibration = best[2]
    return best[1], best[2]


def run_benchmark(fixture_path, index_path, model_name, calibration_output=None):
    rows = load_fixture(fixture_path)
    categories = load_categories(index_path)
    profiles = load_category_profiles()
    engine = NLPEngine(categories=categories, category_profiles=profiles, model_name=model_name)
    metrics, calibration = tune_calibration(
        rows,
        engine,
        calibration_output or PROJECT_DIR / "routing_calibration.json",
    )
    predictions = []
    for case in rows:
        text = build_case_text(case)
        predicted, score, _ = engine.get_topic_details(text)
        candidates = engine.get_category_candidates(text, top_k=2)
        predictions.append(
            {
                "current_name": case["current_name"],
                "expected_category": case["expected_category"],
                "predicted_category": predicted,
                "score": round(score, 4),
                "top2": [item["category"] for item in candidates],
                "expected_coarse_group": case.get("expected_coarse_group"),
                "predicted_coarse_group": infer_coarse_group(predicted),
            }
        )
    metrics["calibration"] = calibration
    metrics["sample_predictions"] = predictions[:15]
    return metrics
