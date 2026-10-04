import argparse
import os
import sys

from johnny.rename.rename_engine import ContextualRenamer


def main():
    parser = argparse.ArgumentParser(description="Rename files using context, content, and use.")
    parser.add_argument("target_path", type=str, help="Path to a file, folder, or drive root to rename.")
    parser.add_argument("--index", type=str, default=os.path.join("runs", "session_Downloads", "master_index.json"), help="Path to master_index.json for semantic context.")
    parser.add_argument("--dry-run", action="store_true", help="Preview renames without changing files.")
    parser.add_argument("--force", action="store_true", help="Rename files even if the current name already looks meaningful.")
    parser.add_argument("--context-label", type=str, help="Optional fixed context label to use in generated names for this run.")
    parser.add_argument("--serial-as-suffix", action="store_true", help="Keep detected serial tokens at the end of the generated name.")
    args = parser.parse_args()

    target_path = args.target_path
    if not os.path.exists(target_path):
        print(f"Error: Path does not exist: {target_path}")
        sys.exit(1)

    index_path = args.index if os.path.isabs(args.index) else os.path.abspath(args.index)
    renamer = ContextualRenamer(index_path=index_path if os.path.exists(index_path) else None)
    results = renamer.rename_target(
        target_path,
        dry_run=args.dry_run,
        force=args.force,
        context_label=args.context_label,
        serial_as_suffix=args.serial_as_suffix,
    )

    renamed = 0
    skipped = 0
    for result in results:
        if result["status"] in {"renamed", "would_rename"}:
            renamed += 1
            print(f'{result["status"].upper()}: {result["old_name"]} -> {result["new_name"]}')
        else:
            skipped += 1

    print(f"Completed. renamed={renamed} skipped={skipped}")


if __name__ == "__main__":
    main()
