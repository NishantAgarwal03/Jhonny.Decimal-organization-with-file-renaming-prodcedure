import csv
import os
import sys
import time
from collections import Counter

from johnny.core.evidence_engine import (
    build_file_evidence,
    build_theme_evidence,
    load_noise_config,
    write_llm_handoff_json,
    write_theme_evidence_csv,
)


def write_legacy_theme_files(session_dir, single_rows):
    themes_file = os.path.join(session_dir, "top_drive_themes.txt")
    tfidf_file = os.path.join(session_dir, "top_drive_themes_tfidf.txt")

    with open(themes_file, "w", encoding="utf-8") as t_out:
        t_out.write("Global Unique Words & Counts:\n")
        t_out.write("-" * 30 + "\n")
        for row in sorted(single_rows, key=lambda item: item["raw_count"], reverse=True):
            t_out.write(f'{row["term"]}: {int(round(float(row["raw_count"])))}\n')

    with open(tfidf_file, "w", encoding="utf-8") as out:
        out.write("Word\tTF-IDF Weight\n")
        out.write("-" * 30 + "\n")
        for row in single_rows:
            out.write(f'{row["term"]}\t{float(row["adjusted_weight"]):.4f}\n')


def main():
    if len(sys.argv) < 2:
        print("Error: Must provide the session folder name (e.g. python drive_context_analyzer.py session_E_Drive)")
        return

    start = time.time()
    session_dir = sys.argv[1]
    csv_file = os.path.join(session_dir, "drive_scan.csv")
    audit_file = os.path.join(session_dir, "mapping_audit.csv")
    evidence_file = os.path.join(session_dir, "top_drive_theme_evidence.csv")
    handoff_file = os.path.join(session_dir, "llm_master_index_handoff.json")
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "theme_noise_config.json")

    if not os.path.exists(csv_file):
        print(f"Error: {csv_file} not found. Run scan_drive.py first.")
        return

    noise_config = load_noise_config(config_path)
    raw_counter = Counter()

    print(f"Extracting typed contextual evidence from {csv_file}...")

    count = 0
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
                "Filename Tokens",
                "Immediate Parent Tokens",
                "Higher Path Tokens",
                "Candidate Tokens",
                "Format/Package Tokens",
                "Cluster Root",
            ]
        )

        for row in reader:
            evidence = build_file_evidence(row, noise_config)

            raw_counter.update(evidence["candidate_tokens"])
            writer.writerow(
                [
                    evidence["filepath"],
                    evidence["filename"],
                    evidence["ext"],
                    ", ".join(evidence["filename_tokens"]),
                    ", ".join(evidence["parent_tokens"]),
                    ", ".join(evidence["higher_path_tokens"]),
                    ", ".join(evidence["candidate_tokens"]),
                    ", ".join(evidence["format_package_tokens"]),
                    evidence["cluster_root"],
                ]
            )

            count += 1
            if count % 10000 == 0:
                print(f"Extracted typed evidence for {count} files...")

    print(f"Typed audit complete for {count} files. Building weighted evidence...")

    single_rows, phrase_rows, cluster_bundle_flags = build_theme_evidence(audit_file, noise_config)
    write_theme_evidence_csv(evidence_file, single_rows, phrase_rows)
    write_legacy_theme_files(session_dir, single_rows)
    write_llm_handoff_json(
        handoff_file,
        os.path.basename(os.path.normpath(session_dir)),
        single_rows,
        phrase_rows,
        cluster_bundle_flags,
        count,
    )

    elapsed = time.time() - start
    print(f"Finished in {elapsed:.2f} seconds.")
    print(f"Typed file-level audit saved to {audit_file}")
    print(f"Canonical evidence saved to {evidence_file}")
    print(f"LLM handoff saved to {handoff_file}")
    print(f"Legacy theme outputs refreshed in {session_dir}")


if __name__ == "__main__":
    main()
