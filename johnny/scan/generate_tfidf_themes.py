import os
import sys

from johnny.core.evidence_engine import read_theme_evidence_csv


def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_tfidf_themes.py <session_folder>")
        return

    session = sys.argv[1]
    evidence_path = os.path.join(session, "top_drive_theme_evidence.csv")
    out_path = os.path.join(session, "top_drive_themes_tfidf.txt")

    if not os.path.exists(evidence_path):
        print(f"Error: {evidence_path} not found. Run drive_context_analyzer.py first.")
        return

    rows = read_theme_evidence_csv(evidence_path)
    single_rows = [row for row in rows if row.get("token_type") == "single"]

    with open(out_path, "w", encoding="utf-8") as out:
        out.write("Word\tTF-IDF Weight\n")
        out.write("-" * 30 + "\n")
        for row in single_rows:
            out.write(f'{row["term"]}\t{float(row["adjusted_weight"]):.4f}\n')
    print(f"Weighted TF-IDF themes written to {out_path}")


if __name__ == "__main__":
    main()
