import os
import argparse
import sys
import math
from johnny.core.index_manager import IndexManager
from johnny.core.planner import REVIEW_DIR, SAFE_DIR, SOFTWARE_DIR, is_managed_dir, relative_parent, split_files
from johnny.core.nlp_engine import NLPEngine
from johnny.organize.router import Router
from johnny.rename.rename_engine import ContextualRenamer

def main():
    parser = argparse.ArgumentParser(description="Subject-based Clustering Agent")
    parser.add_argument("target_dir", type=str, help="Path to the directory containing unsorted files.")
    parser.add_argument("--index", type=str, default="master_index.json", help="Path to master_index.json")
    parser.add_argument("--read-only", action="store_true", help="Scan only to build master_index without moving or renaming.")
    parser.add_argument("--fast-seed", action="store_true", help="Use file names for topics to bypass full text parsing.")
    args = parser.parse_args()

    target_dir = args.target_dir
    index_path = os.path.abspath(args.index)

    if not os.path.isdir(target_dir):
        print(f"Error: Directory {target_dir} does not exist.")
        sys.exit(1)

    print(f"Starting Subject-based Clustering on: {target_dir}")
    print(f"Using index: {index_path}")


    index_manager = IndexManager(args.index)
    categories = list(index_manager.data.get('categories', {}).keys())
    nlp = NLPEngine(categories, category_profiles=index_manager.profiles)
    renamer = ContextualRenamer(index_path=args.index)
    
    # We will use the directory where master_index.json lives as the JD root.
    jd_root_dir = os.path.dirname(args.index) or '.'
    router = Router(index_manager, jd_root_dir)

    planned_items = []

    def on_error(err):
        pass

    collected = []
    for root, dirs, files in os.walk(args.target_dir, onerror=on_error):
        dirs[:] = [d for d in dirs if not is_managed_dir(d)]
        for file in files:
            if not file.startswith('.'):
                collected.append(os.path.join(root, file))

    units, junk, routed_paths = split_files(args.target_dir, collected)

    for file_path in routed_paths:
        file = os.path.basename(file_path)
        root = os.path.dirname(file_path)
        if args.fast_seed:
            base, _ = os.path.splitext(file)
            clean_name = base.replace('_', ' ').replace('-', ' ')
            clean_root = root.replace('\\', ' ').replace('/', ' ').replace('_', ' ').replace('-', ' ')
            context_text = f"{clean_name} {clean_root}"
            topic = nlp.get_topic(context_text)
        else:
            text = nlp.extract_text(file_path)
            if not text.strip():
                print(f"Skipping {file}: No text extracted.")
                continue
            topic = nlp.get_topic(text)

        planned_items.append((file_path, file, topic))

    print(
        f"Plan: routed={len(planned_items)} software_units={len(units)} ({sum(units.values())} files) "
        f"junk_safe={sum(1 for t in junk.values() if t == 'safe')} junk_review={sum(1 for t in junk.values() if t == 'review')} "
        "(units and junk are handled by rules and are not counted by the 1/150 guard)"
    )

    if not args.read_only:
        unsorted_count = sum(1 for _, _, topic in planned_items if topic == "Unsorted_Miscellaneous")
        allowed_unsorted = math.floor(len(planned_items) / 150) if planned_items else 0
        if unsorted_count > allowed_unsorted:
            print(
                f"Apply blocked: Unsorted_Miscellaneous={unsorted_count} exceeds 1/150 limit ({allowed_unsorted}) "
                f"for {len(planned_items)} routed files."
            )
            sys.exit(2)

    for unit, count in units.items():
        if args.read_only:
            print(f"[Read-Only] Would move software folder as one unit ({count} files): {os.path.basename(unit)}")
        else:
            router.route_special(unit, SOFTWARE_DIR, relative_parent(args.target_dir, unit))
    for file_path, tier in junk.items():
        if args.read_only:
            print(f"[Read-Only] Would move {os.path.basename(file_path)} to '{SAFE_DIR if tier == 'safe' else REVIEW_DIR}'")
        else:
            router.route_special(file_path, SAFE_DIR if tier == "safe" else REVIEW_DIR, relative_parent(args.target_dir, file_path))

    for file_path, file, topic in planned_items:
        if args.read_only:
            print(f"[Read-Only] Would route {file} to '{topic}'")
            continue

        if args.fast_seed:
            print(f"Fast-seeding {file} -> '{topic}'")
            router.route_file(file_path, topic)
            continue

        rename_result = renamer.rename_file(file_path, dry_run=False, force=False)
        if rename_result.get("status") == "renamed":
            print(f'Renamed: {rename_result["old_name"]} -> {rename_result["new_name"]}')
            file_path = rename_result["new_path"]
        router.route_file(file_path, topic)

if __name__ == "__main__":
    main()
