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
    SOFTWARE_DIR: "Whole software and environment folders (virtual environments, node_modules, git projects), moved as one unit.\nMove a folder back to restore it, or run: python -m johnny.core.utils --apply\n",
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


def folder_vote(paths, topics, closest, unsorted="Unsorted_Miscellaneous", share=0.8, min_files=20, hold=None, type_check=False, stats=None):
    """Files the model refused follow their folder.

    paths: relative to the scanned folder; topics: each file's final bucket; closest: the bucket each file was nearest to,
    even when refused (parallel lists). A refused file joins the bucket of its deepest folder that has `min_files`+ files
    where at least `share` of them are nearest to the same bucket; mixed folders have no such bucket and stay as they are.
    -> (new topics, number of files moved by the vote)

    Tried and dropped (require_own_match): a refused file follows only if its own closest bucket equals the folder's winner.
    Synthetic judge, seeds 7 and 99, both indexes: correct -1.2 to -1.9 points, wrong only -0.1 to -0.3 (round 2: 2.4% -> 2.1%,
    2.1% -> 1.9%); the one case it fixes is decoy-minority (wrong 18.2% -> 6.8%, 9.3% -> 2.3%); overlap barely moves
    (33.8% -> 31.2%). Real scans, files handed back to the next round: E: 1,646 to 1,890 (Unsorted 5,076 -> 6,722 and
    15,139 -> 17,029), Downloads 206 (811 -> 1,017). About 5 to 9 correct sorts are given up per wrong folder avoided, the same
    exchange rate as raising the lead, so it only moves along the trade-off curve. Not adopted.

    Known risk: it spreads a mistake the model makes consistently across a whole folder (agreement cannot detect that), so the
    preview must be read before Apply. share=0.8 and min_files=20 were chosen on one drive and are not calibrated; the synthetic
    judge (tests/synthetic_benchmark.py) shows what the vote buys and costs."""
    dirs = [[part for part in path.replace("/", os.sep).split(os.sep)[:-1] if part] for path in paths]
    votes = {}
    for parts, near in zip(dirs, closest):
        for depth in range(1, len(parts) + 1):
            votes.setdefault(tuple(parts[:depth]), {}).setdefault(near, 0)
            votes[tuple(parts[:depth])][near] += 1
    exts = [os.path.splitext(path)[1].lower() for path in paths]
    winner = {}
    for key, counts in votes.items():
        total = sum(counts.values())
        best = max(counts, key=counts.get)
        winner[key] = best if total >= min_files and best != unsorted and counts[best] / total >= share else None
    # type_check: the file's type must also appear among the files that agreed on the winning bucket (at least 2 files and 5% of
    # them), so a PDF does not follow a folder of songs. hold[i]=True keeps file i out of the vote (see has_information).
    support = {}
    if type_check:
        for parts, near, ext in zip(dirs, closest, exts):
            for depth in range(1, len(parts) + 1):
                key = tuple(parts[:depth])
                if near == winner[key]:
                    support.setdefault(key, {}).setdefault(ext, 0)
                    support[key][ext] += 1
    out, moved = list(topics), 0
    for i, (parts, topic) in enumerate(zip(dirs, topics)):
        if topic != unsorted or (hold and hold[i]):
            continue
        for depth in range(len(parts), 0, -1):
            key = tuple(parts[:depth])
            if winner[key]:
                seen = support.get(key, {})
                if not type_check or seen.get(exts[i], 0) >= max(2, 0.05 * sum(seen.values())):
                    out[i] = winner[key]
                    moved += 1
                elif stats is not None:
                    stats["blocked_by_type"] = stats.get("blocked_by_type", 0) + 1
                break
    return out, moved


# Words that say nothing about what a file is: names made by cameras, scanners and editors, and "junk drawer" folder names.
_GENERIC_WORDS = set("""scan scanned scanner document documents doc docs image images img photo photos pic pics picture pictures file files
download downloads copy new untitled microsoft word excel text screenshot dsc pxl vid video audio recording whatsapp tmp temp
backup misc other stuff old folder desktop data pdf docx jpg jpeg png page""".split())


def has_information(path):
    """False when no word of 3+ letters in the folders or the name says anything (scan7300.pdf in Old Stuff, IMG_4213.jpg in
    Downloads): such a file cannot be sorted by name or path, so guessing is the only way to move it."""
    stem = os.path.splitext(path)[0]
    return any(w.lower() not in _GENERIC_WORDS for w in re.findall(r"[A-Za-z]{3,}", stem))


def relative_parent(target_dir, path):
    """Folder of `path` relative to the scan target, so moved items keep their origin ('.' at the top)."""
    rel = os.path.relpath(os.path.dirname(os.path.abspath(path)), os.path.abspath(target_dir))
    return "." if rel.startswith("..") else rel


def write_note(folder_path, folder_name):
    note = os.path.join(folder_path, "_README.txt")
    if folder_name in NOTES and not os.path.exists(note):
        with open(note, "w", encoding="utf-8") as f:
            f.write(NOTES[folder_name])
