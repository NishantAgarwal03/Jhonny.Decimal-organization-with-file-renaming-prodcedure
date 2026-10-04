"""Rule-based pre-pass shared by organizer.py and the UI.

Before any semantic routing, files are split into three groups so the router only sees real documents:
  - units:  software / environment trees (venv, site-packages, node_modules, .git projects), moved whole as one item;
  - junk:   temporary or regenerable files, moved to a clearly named folder (never deleted);
  - routed: everything else, which goes through the semantic router and the 1/150 guard.
Units and junk are handled by fixed rules, so they are not counted by the guard (they are reported separately).
"""
import os
import re

SAFE_DIR = "_Temp_Safe_To_Remove"
REVIEW_DIR = "_Temp_Review_First"
SOFTWARE_DIR = "_Software_Projects"
SPECIAL_DIRS = (SAFE_DIR, REVIEW_DIR, SOFTWARE_DIR)

NOTES = {
    SAFE_DIR: "Temporary or regenerable files (thumbnail caches, Office lock files, *.tmp, *.pyc).\nSafe to remove after a quick look. Original folder structure is kept below.\n",
    REVIEW_DIR: "Backups, logs, partial downloads and dumps (*.bak, *.old, *.log, *.part, *.crdownload ...).\nCheck before removing: a backup may be the only copy. Original folder structure is kept below.\n",
    SOFTWARE_DIR: "Whole software and environment folders (virtual environments, node_modules, git projects), moved as one unit.\nMove a folder back to restore it, or run: python utils.py --apply\n",
}

_JD_DIR = re.compile(r"^\d{2}\.\d{2}_")
_SAFE_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
_SAFE_EXT = {".tmp", ".temp", ".pyc", ".pyo"}
_REVIEW_EXT = {".bak", ".old", ".log", ".dmp", ".part", ".crdownload", ".swp", ".chk", ".cache"}
_ENV_DIRS = {"venv", ".venv", "env", ".env", "virtualenv"}


def is_managed_dir(name):
    """Folders this tool created (JD category folders and the special folders): never planned again on a re-run."""
    return name in SPECIAL_DIRS or bool(_JD_DIR.match(name))


def junk_tier(file_name):
    """'safe' (regenerable), 'review' (could be the only copy) or None."""
    lowered = file_name.lower()
    ext = os.path.splitext(lowered)[1]
    if lowered in _SAFE_NAMES or lowered.startswith("~$") or ext in _SAFE_EXT:
        return "safe"
    if ext in _REVIEW_EXT:
        return "review"
    return None


def bundle_root_parts(dir_parts):
    """How many leading directory parts form a software/environment unit (0 = none).

    The unit is the project folder that holds the marker, so a project and its environment move together.
    A marker at the very top of the scanned folder (0) is ignored: the scan target itself is never a unit.
    """
    lowered = [part.lower() for part in dir_parts]
    roots = []
    for i, part in enumerate(lowered):
        if part in (".git", "node_modules"):
            roots.append(i)
        elif part == "site-packages":
            lib = next((j for j in range(i - 1, -1, -1) if lowered[j] in ("lib", "lib64")), None)
            if lib is None:
                roots.append(i)
            else:
                roots.append(lib - 1 if lib >= 1 and lowered[lib - 1] in _ENV_DIRS else lib)
    roots = [r for r in roots if r > 0]
    return min(roots) if roots else 0


def split_files(target_dir, file_paths):
    """-> (units {dir: file_count}, junk {path: tier}, routed [paths]); the three groups are disjoint."""
    target = os.path.abspath(target_dir)
    parsed = []
    roots = set()
    for path in file_paths:
        rel = os.path.relpath(os.path.abspath(path), target)
        parts = tuple(rel.split(os.sep)) if rel != "." and not rel.startswith("..") else ()
        parsed.append((path, parts))
        count = bundle_root_parts(list(parts[:-1])) if parts else 0
        if count:
            roots.add(parts[:count])
    # Every file under a unit root belongs to the unit (the project's own sources, not only its environment).
    units, junk, routed = {}, {}, []
    for path, parts in parsed:
        root = next((parts[:k] for k in range(1, len(parts)) if parts[:k] in roots), None)
        if root:
            unit = os.path.join(target, *root)
            units[unit] = units.get(unit, 0) + 1
        elif parts and junk_tier(parts[-1]):
            junk[path] = junk_tier(parts[-1])
        else:
            routed.append(path)
    return units, junk, routed


def relative_parent(target_dir, path):
    """Folder of `path` relative to the scan target, so moved items keep their origin ('.' at the top)."""
    rel = os.path.relpath(os.path.dirname(os.path.abspath(path)), os.path.abspath(target_dir))
    return "." if rel.startswith("..") else rel


def write_note(folder_path, folder_name):
    note = os.path.join(folder_path, "_README.txt")
    if folder_name in NOTES and not os.path.exists(note):
        with open(note, "w", encoding="utf-8") as f:
            f.write(NOTES[folder_name])
