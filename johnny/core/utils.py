import os
import json
import shutil
import datetime
from pathlib import Path

def default_sorted_root(today=None):
    """Where sorted files go when the user names no folder: Desktop\Sorted JD_<date> (the home folder if there is no Desktop)."""
    home = Path.home()
    return str((home / "Desktop" if (home / "Desktop").is_dir() else home) / f"Sorted JD_{today or datetime.date.today().isoformat()}")

def unique_path(path, reserved=None):
    """
    The one collision rule for every rename and move: if `path` is taken, add _1, _2, ... before the
    extension. No clock, so a dry run and the apply that follows pick the same names. `reserved` (a set)
    holds names already chosen in this run.
    """
    reserved = set() if reserved is None else reserved
    base, ext = os.path.splitext(path)
    candidate, counter = path, 0
    while os.path.exists(candidate) or os.path.normcase(candidate) in reserved:
        counter += 1
        candidate = f"{base}_{counter}{ext}"
    reserved.add(os.path.normcase(candidate))
    return candidate

def generate_safe_filename(target_dir, base_name, ext, max_length=170, reserved=None):
    """A collision-free path in target_dir for base_name + ext (name cut to max_length first)."""
    if len(base_name) > max_length:
        base_name = base_name[:max_length].strip()
    return unique_path(os.path.join(target_dir, f"{base_name}{ext}"), reserved)

def sanitize_topic_name(topic):
    """
    Sanitizes string to be a safely valid folder name.
    """
    safe_chars = [c if c.isalnum() else '_' for c in topic]
    clean = "".join(safe_chars)
    # Remove multiple underscores
    import re
    clean = re.sub(r'_+', '_', clean)
    return clean.strip('_').title()


UNDO_LOG = os.environ.get("JOHNNY_UNDO_LOG") or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "undo_log.jsonl")


def safe_rename(src, dst, log_path=None):
    """
    The single primitive for every rename and move. Refuses to overwrite, then appends
    {ts, old, new} to the undo log (JSONL) once the move has succeeded.
    """
    if os.path.exists(dst) and not os.path.samefile(src, dst):
        raise FileExistsError(f"Refusing to overwrite: {dst}")
    shutil.move(src, dst)
    entry = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "old": os.path.abspath(src), "new": os.path.abspath(dst)}
    with open(log_path or UNDO_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return dst


def undo_renames(log_path=None, dry_run=True):
    """
    Reverses logged renames/moves newest-first. Skips (never overwrites) when the
    original path is occupied or the moved file is gone. Reversed entries are removed from the log.
    """
    log_path = log_path or UNDO_LOG
    if not os.path.exists(log_path):
        return []
    with open(log_path, encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]
    remaining, results = [], []
    for entry in reversed(entries):
        if not os.path.exists(entry["new"]):
            status = "skipped_missing"
        elif os.path.exists(entry["old"]):
            status = "skipped_occupied"
        elif dry_run:
            status = "would_restore"
        else:
            os.makedirs(os.path.dirname(entry["old"]), exist_ok=True)
            shutil.move(entry["new"], entry["old"])
            status = "restored"
        if status in {"skipped_missing", "skipped_occupied", "would_restore"}:
            remaining.append(entry)
        results.append({**entry, "status": status})
    if not dry_run:
        with open(log_path, "w", encoding="utf-8") as f:
            for entry in reversed(remaining):
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return results


if __name__ == "__main__":
    import sys
    apply_changes = "--apply" in sys.argv
    for r in undo_renames(dry_run=not apply_changes):
        print(f'{r["status"].upper()}: {r["new"]} -> {r["old"]}')
    if not apply_changes:
        print("Dry run. Re-run with --apply to restore.")
