import csv
import json
import os
import time
import sys
from collections import Counter

from johnny.core.embedding_manager import load_routing_calibration
from johnny.core.index_manager import IndexManager
from johnny.core.nlp_engine import NLPEngine
from johnny.core.evidence_engine import read_theme_evidence_csv


LOW_CONFIDENCE_THRESHOLD = load_routing_calibration()["dense_confidence_threshold"]
OVERBROAD_CATEGORY_THRESHOLD = 0.40


def load_category_profiles(session_dir):
    handoff_path = os.path.join(session_dir, "llm_master_index_handoff.json")
    if not os.path.exists(handoff_path):
        return {}

    with open(handoff_path, "r", encoding="utf-8") as f:
        handoff = json.load(f)

    category_map = handoff.get("category_term_map", {})
    profiles = {}
    for category, mapping in category_map.items():
        descriptor_parts = [category.replace("_", " ")]
        descriptor_parts.extend(mapping.get("descriptor_terms", []))
        descriptor_parts.extend(mapping.get("key_phrases", []))
        profiles[category] = " ".join(descriptor_parts)
    return profiles


def build_validation_summary(session_dir, category_counter, low_confidence_count, total_count):
    summary_path = os.path.join(session_dir, "mapping_validation_summary.json")
    evidence_path = os.path.join(session_dir, "top_drive_theme_evidence.csv")
    master_index_path = os.path.join(session_dir, "master_index.json")

    category_share = {
        category: {"count": count, "share": round(count / total_count, 4)}
        for category, count in category_counter.most_common()
    }
    likely_overbroad = [
        {"category": category, "count": count, "share": round(count / total_count, 4)}
        for category, count in category_counter.items()
        if total_count and (count / total_count) >= OVERBROAD_CATEGORY_THRESHOLD
    ]

    missing_categories = []
    if os.path.exists(evidence_path) and os.path.exists(master_index_path):
        evidence_rows = read_theme_evidence_csv(evidence_path)
        with open(master_index_path, "r", encoding="utf-8") as f:
            master_index = json.load(f)
        category_text = " ".join(master_index.get("categories", {}).keys()).lower()
        for row in evidence_rows:
            term = row.get("term", "").lower()
            if not term or term in category_text:
                continue
            if float(row.get("adjusted_weight", 0)) < 1500:
                continue
            if row.get("noise_labels"):
                continue
            missing_categories.append(
                {
                    "term": row["term"],
                    "token_type": row["token_type"],
                    "adjusted_weight": float(row["adjusted_weight"]),
                    "sample_files": row["sample_files"],
                }
            )
            if len(missing_categories) >= 25:
                break

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_files": total_count,
        "unsorted_ratio": round(category_counter.get("Unsorted_Miscellaneous", 0) / total_count, 6) if total_count else 0.0,
        "low_confidence_ratio": round(low_confidence_count / total_count, 6) if total_count else 0.0,
        "top_categories": category_share,
        "likely_overbroad_categories": likely_overbroad,
        "likely_missing_categories": missing_categories,
    }

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    return summary_path, summary


def main():
    if len(sys.argv) < 2:
        print("Error: Must provide the session folder name (e.g. python fast_seed_csv.py session_E_Drive)")
        return

    session_dir = sys.argv[1]
    start_time = time.time()
    csv_file = os.path.join(session_dir, "drive_scan.csv")
    index_file = os.path.join(session_dir, "master_index.json")
    audit_file = os.path.join(session_dir, "mapping_audit_v2.csv")

    if not os.path.exists(index_file):
        print(f"Error: {index_file} not found. Please ensure the framework is created.")
        return

    index_manager = IndexManager(index_file)
    categories = list(index_manager.data.get("categories", {}).keys())
    category_profiles = load_category_profiles(session_dir)

    print("Loading Semantic AI Model (Sentence-Transformers)...")
    nlp = NLPEngine(categories, category_profiles=category_profiles)

    print(f"Loaded {len(categories)} categories. Processing CSV and generating audit report...")

    count = 0
    low_confidence_count = 0
    category_counter = Counter()
    with open(csv_file, "r", encoding="utf-8", newline="") as f_in, open(
        audit_file, "w", newline="", encoding="utf-8"
    ) as f_out:
        reader = csv.DictReader(f_in)
        writer = csv.writer(f_out)

        writer.writerow(
            [
                "File Path",
                "File Name",
                "File Type",
                "Cleaned Text (Keywords)",
                "Confidence Score (%)",
                "Assigned Category",
                "JD Number",
                "Low Confidence Flag",
                "Category Profile Used",
            ]
        )

        for row in reader:
            filepath = row["path"] if "path" in row else "Unknown"
            filename = row["name"]
            ext = row["type"]

            clean_path = filepath.replace("\\", " ").replace("/", " ").replace("_", " ").replace("-", " ")
            clean_name = filename.replace("_", " ").replace("-", " ")
            context_text = f"{clean_name} {clean_path}"

            topic, score, clean_text = nlp.get_topic_details(context_text)
            jd_number = index_manager.get_category_prefix(topic) or "Unknown"
            confidence_pct = f"{score * 100:.2f}%"
            low_confidence = "__YES__" if score < LOW_CONFIDENCE_THRESHOLD else "__NO__"
            profile_used = category_profiles.get(topic, topic)

            writer.writerow([filepath, filename, ext, clean_text, confidence_pct, topic, jd_number, low_confidence, profile_used])

            category_counter[topic] += 1
            if low_confidence == "__YES__":
                low_confidence_count += 1

            count += 1
            if count % 5000 == 0:
                print(f"Processed and audited {count} files...")

    summary_path, summary = build_validation_summary(session_dir, category_counter, low_confidence_count, count)

    elapsed = time.time() - start_time
    print(f"Finished mapping audit of {count} files in {elapsed:.2f} seconds.")
    print(f"Report saved to {audit_file}")
    print(f"Validation summary saved to {summary_path}")
    print(
        "Validation metrics: "
        f"unsorted={summary['unsorted_ratio']:.4%}, "
        f"low_confidence={summary['low_confidence_ratio']:.4%}"
    )


if __name__ == "__main__":
    main()
