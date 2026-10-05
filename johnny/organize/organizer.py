import os
import argparse
import sys
from johnny.core.index_manager import IndexManager
from johnny.core.planner import REVIEW_DIR, SAFE_DIR, SOFTWARE_DIR, is_managed_dir
from johnny.core.utils import default_sorted_root
from johnny.core.sorting import plan_organization, plan_text, presets
from johnny.core.nlp_engine import NLPEngine
from johnny.organize.router import Router
from johnny.rename.rename_engine import ContextualRenamer

def main():
    parser = argparse.ArgumentParser(description="Subject-based Clustering Agent")
    parser.add_argument("target_dir", type=str, help="Path to the directory containing unsorted files.")
    parser.add_argument("--index", type=str, default="master_index.json", help="Path to master_index.json")
    parser.add_argument("--dest", type=str, default=None, help="Folder the sorted JD folders are created in (default: Desktop\Sorted JD_<date>).")
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
    
    # Sorted files go to --dest, never silently into the index's folder; the folder is created only when a file is moved.
    jd_root_dir = os.path.abspath(args.dest) if args.dest else default_sorted_root()
    print(f"Sorted files go to: {jd_root_dir}")
    router = Router(index_manager, jd_root_dir)

    def on_error(err):
        pass

    collected = []
    for root, dirs, files in os.walk(args.target_dir, onerror=on_error):
        dirs[:] = [d for d in dirs if not is_managed_dir(d)]
        for file in files:
            if not file.startswith('.'):
                collected.append(os.path.join(root, file))

    def text_of(file_path):
        # The UI's name + path + type text; document text is an extra clue, and a file without any is still sorted.
        extra = "" if args.fast_seed else nlp.extract_text(file_path).strip()
        return f"{plan_text(file_path)} {extra}".strip()

    planned_items, _, stats = plan_organization(index_manager, nlp, args.target_dir, collected, presets()["second"], text_of=text_of)
    units = [i for i in planned_items if i["destination"] == SOFTWARE_DIR]
    junk = [i for i in planned_items if i["topic"] is None and i["destination"] != SOFTWARE_DIR]
    routed_items = [i for i in planned_items if i["topic"] is not None]

    print(
        f"Plan: routed={len(routed_items)} software_units={len(units)} ({sum(i['count'] for i in units)} files) "
        f"junk_safe={sum(1 for i in junk if i['destination'] == SAFE_DIR)} junk_review={sum(1 for i in junk if i['destination'] == REVIEW_DIR)} "
        "(units and junk are handled by rules and are not counted by the 1/150 guard)"
    )

    if not args.read_only and stats["unsorted"] > stats["allowed"]:
        print(
            f"Apply blocked: Unsorted_Miscellaneous={stats['unsorted']} exceeds 1/150 limit ({stats['allowed']}) "
            f"for {len(routed_items)} routed files."
        )
        sys.exit(2)

    for item in units + junk:
        name = os.path.basename(item["path"])
        if not args.read_only:
            router.route_special(item["path"], item["destination"], item["rel"])
        elif item["destination"] == SOFTWARE_DIR:
            print(f"[Read-Only] Would move software folder as one unit ({item['count']} files): {name}")
        else:
            print(f"[Read-Only] Would move {name} to '{item['destination']}'")

    for item in routed_items:
        file_path, file, topic = item["path"], os.path.basename(item["path"]), item["topic"]
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

    if not args.read_only:
        print(f"Done: {len(planned_items)} item(s) sorted into {jd_root_dir}. Undo with: python -m johnny.core.utils --apply")

if __name__ == "__main__":
    main()
