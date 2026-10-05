import csv
import json
import math
import os
import re
from collections import Counter, defaultdict


DEFAULT_CONFIG = {
    "global_stopwords": [
        "and",
        "the",
        "for",
        "with",
        "copy",
        "document",
        "untitled",
        "new",
        "folder",
        "file",
        "from",
        "to",
        "of",
        "in",
        "on",
        "a",
        "an",
        "by",
        "or",
    ],
    "path_scaffolding_terms": [
        "users",
        "user",
        "admin",
        "desktop",
        "downloads",
        "documents",
        "drive",
        "onedrive",
        "session",
        "root",
    ],
    "packaging_terms": [
        "tmp",
        "cache",
        "caches",
        "resources",
        "bin",
        "lib",
        "libs",
        "share",
        "common",
        "data",
        "objects",
        "deps",
        "build",
        "dist",
        "portable",
        "release",
        "debug",
        "installer",
        "install",
        "setup",
        "portable",
        "x64",
        "x86",
        "64",
        "32",
        "windows",
        "node_modules",
        "__pycache__",
    ],
    "format_terms": [
        "json",
        "xml",
        "dll",
        "exe",
        "zip",
        "png",
        "jpg",
        "jpeg",
        "svg",
        "pdf",
        "doc",
        "docx",
        "txt",
        "csv",
        "xlsx",
        "xls",
        "pyc",
    ],
}

SOURCE_WEIGHTS = {
    "filename": 3.0,
    "parent": 2.2,
    "higher_path": 1.4,
    "format_package": 0.6,
    "phrase_filename": 3.2,
    "phrase_path": 2.0,
}

PACKAGING_MARKERS = {
    "resources",
    "cache",
    "caches",
    "tmp",
    "bin",
    "lib",
    "share",
    "common",
    "objects",
    "deps",
    "build",
    "dist",
    "__pycache__",
}


def load_noise_config(config_path=None):
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if config_path and os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        for key, values in user_config.items():
            if isinstance(values, list):
                merged = {str(v).lower() for v in config.get(key, [])}
                merged.update(str(v).lower() for v in values)
                config[key] = sorted(merged)
            else:
                config[key] = values
    return config


def tokenize_text(text):
    return re.findall(r"[a-zA-Z0-9]{3,}", (text or "").lower())


def split_path_components(path_value):
    clean = (path_value or "").replace("/", "\\")
    return [p for p in clean.split("\\") if p]


def classify_token(token, noise_config):
    labels = []
    if token in noise_config.get("global_stopwords", []):
        labels.append("stopword")
    if token in noise_config.get("path_scaffolding_terms", []):
        labels.append("path_scaffolding")
    if token in noise_config.get("packaging_terms", []):
        labels.append("packaging")
    if token in noise_config.get("format_terms", []):
        labels.append("format")
    return labels


def build_cluster_root(path_value, noise_config, max_parts=3):
    parts = split_path_components(path_value)
    meaningful = []
    for part in parts[:-1]:
        tokens = tokenize_text(part)
        filtered = [
            token
            for token in tokens
            if "path_scaffolding" not in classify_token(token, noise_config)
        ]
        if filtered:
            meaningful.append("_".join(filtered[:2]))
        if len(meaningful) >= max_parts:
            break
    return " > ".join(meaningful) if meaningful else "root"


def build_file_evidence(row, noise_config):
    filepath = row.get("path", "")
    filename = row.get("name", "")
    ext = row.get("type", "")
    path_parts = split_path_components(filepath)
    parent_part = path_parts[-2] if len(path_parts) >= 2 else ""
    higher_parts = path_parts[:-2]

    filename_tokens = tokenize_text(filename)
    parent_tokens = tokenize_text(parent_part)
    higher_tokens = []
    for part in higher_parts:
        higher_tokens.extend(tokenize_text(part))

    format_package_tokens = []
    if ext:
        format_package_tokens.extend(tokenize_text(ext))
    for token in filename_tokens + parent_tokens + higher_tokens:
        labels = classify_token(token, noise_config)
        if "packaging" in labels or "format" in labels:
            format_package_tokens.append(token)

    combined = filename_tokens + parent_tokens + higher_tokens
    candidate_tokens = sorted(set(combined + format_package_tokens))
    cluster_root = build_cluster_root(filepath, noise_config)

    return {
        "filepath": filepath,
        "filename": filename,
        "ext": ext,
        "filename_tokens": filename_tokens,
        "parent_tokens": parent_tokens,
        "higher_path_tokens": higher_tokens,
        "format_package_tokens": sorted(set(format_package_tokens)),
        "candidate_tokens": candidate_tokens,
        "cluster_root": cluster_root,
    }


def generate_phrases(tokens, min_size=2, max_size=3):
    phrases = []
    for size in range(min_size, max_size + 1):
        for i in range(len(tokens) - size + 1):
            phrase_tokens = tokens[i : i + size]
            if len(set(phrase_tokens)) == 1:
                continue
            phrases.append(" ".join(phrase_tokens))
    return phrases


def detect_bundle_flags(cluster_root, token_sources):
    total_refs = sum(len(entries) for entries in token_sources.values())
    packaging_refs = 0
    for token, entries in token_sources.items():
        if token in PACKAGING_MARKERS:
            packaging_refs += len(entries)
    if total_refs == 0:
        return {"is_bundle_like": False, "bundle_penalty": 1.0}
    packaging_ratio = packaging_refs / total_refs
    is_bundle_like = total_refs >= 40 and packaging_ratio >= 0.18
    penalty = 0.25 if is_bundle_like else 1.0
    return {"is_bundle_like": is_bundle_like, "bundle_penalty": penalty}


def build_theme_evidence(mapping_audit_path, noise_config):
    token_stats = {}
    phrase_stats = {}
    cluster_token_sources = defaultdict(lambda: defaultdict(list))
    cluster_sizes = Counter()

    with open(mapping_audit_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            filepath = row["File Path"]
            filename = row["File Name"]
            cluster_root = row["Cluster Root"]
            cluster_sizes[cluster_root] += 1

            file_tokens = {
                "filename": [t for t in row["Filename Tokens"].split(", ") if t],
                "parent": [t for t in row["Immediate Parent Tokens"].split(", ") if t],
                "higher_path": [t for t in row["Higher Path Tokens"].split(", ") if t],
                "format_package": [t for t in row["Format/Package Tokens"].split(", ") if t],
            }

            per_file_seen = set()
            for source, tokens in file_tokens.items():
                source_seen = set()
                for token in tokens:
                    if token not in token_stats:
                        token_stats[token] = {
                            "term": token,
                            "token_type": "single",
                            "raw_count": 0.0,
                            "file_presence": 0,
                            "cluster_presence": set(),
                            "source_counts": Counter(),
                            "noise_labels": classify_token(token, noise_config),
                            "sample_paths": [],
                            "sample_files": [],
                        }
                    token_stats[token]["raw_count"] += SOURCE_WEIGHTS[source]
                    token_stats[token]["source_counts"][source] += 1
                    if token not in source_seen:
                        cluster_token_sources[cluster_root][token].append(source)
                        source_seen.add(token)
                    if token not in per_file_seen:
                        token_stats[token]["file_presence"] += 1
                        token_stats[token]["cluster_presence"].add(cluster_root)
                        if len(token_stats[token]["sample_paths"]) < 3:
                            token_stats[token]["sample_paths"].append(filepath)
                            token_stats[token]["sample_files"].append(filename)
                        per_file_seen.add(token)

            phrase_sources = {
                "phrase_filename": generate_phrases(file_tokens["filename"]),
                "phrase_path": generate_phrases(file_tokens["parent"] + file_tokens["higher_path"]),
            }
            phrase_seen = set()
            for source, phrases in phrase_sources.items():
                for phrase in phrases:
                    if phrase not in phrase_stats:
                        phrase_stats[phrase] = {
                            "term": phrase,
                            "token_type": "phrase",
                            "raw_count": 0.0,
                            "file_presence": 0,
                            "cluster_presence": set(),
                            "source_counts": Counter(),
                            "noise_labels": [],
                            "sample_paths": [],
                            "sample_files": [],
                        }
                    phrase_stats[phrase]["raw_count"] += SOURCE_WEIGHTS[source]
                    phrase_stats[phrase]["source_counts"][source] += 1
                    if phrase not in phrase_seen:
                        phrase_stats[phrase]["file_presence"] += 1
                        phrase_stats[phrase]["cluster_presence"].add(cluster_root)
                        if len(phrase_stats[phrase]["sample_paths"]) < 3:
                            phrase_stats[phrase]["sample_paths"].append(filepath)
                            phrase_stats[phrase]["sample_files"].append(filename)
                        phrase_seen.add(phrase)

    cluster_bundle_flags = {
        cluster_root: detect_bundle_flags(cluster_root, token_sources)
        for cluster_root, token_sources in cluster_token_sources.items()
    }
    total_clusters = max(len(cluster_sizes), 1)
    total_files = max(sum(cluster_sizes.values()), 1)

    def finalize_stats(stats_map):
        evidence_rows = []
        for item in stats_map.values():
            cluster_presence_count = len(item["cluster_presence"])
            file_presence = item["file_presence"]
            concentration = 1.0 - (cluster_presence_count / (total_clusters + 1))
            idf = math.log((total_files + 1) / (file_presence + 1)) + 1.0

            bundle_penalty = 1.0
            bundle_cluster_share = 0.0
            for cluster_root in item["cluster_presence"]:
                bundle_cluster_share += 1 if cluster_bundle_flags.get(cluster_root, {}).get("is_bundle_like") else 0
                bundle_penalty = min(
                    bundle_penalty,
                    cluster_bundle_flags.get(cluster_root, {}).get("bundle_penalty", 1.0),
                )
            bundle_cluster_share = bundle_cluster_share / max(cluster_presence_count, 1)
            noise_penalty = 1.0
            labels = set(item["noise_labels"])
            if "stopword" in labels:
                noise_penalty *= 0.15
            if "path_scaffolding" in labels:
                noise_penalty *= 0.35
            if "packaging" in labels:
                noise_penalty *= 0.22
            if "format" in labels:
                noise_penalty *= 0.18

            higher_path_count = item["source_counts"].get("higher_path", 0)
            filename_count = item["source_counts"].get("filename", 0)
            total_sources = sum(item["source_counts"].values()) or 1
            higher_path_ratio = higher_path_count / total_sources
            filename_ratio = filename_count / total_sources
            if bundle_cluster_share >= 0.6 and higher_path_ratio >= 0.6 and filename_ratio < 0.2:
                bundle_penalty *= 0.28
            elif bundle_cluster_share >= 0.6 and higher_path_ratio >= 0.4:
                bundle_penalty *= 0.45

            adjusted_weight = item["raw_count"] * idf * (0.6 + concentration) * noise_penalty * bundle_penalty
            evidence_rows.append(
                {
                    "term": item["term"],
                    "token_type": item["token_type"],
                    "raw_count": round(item["raw_count"], 4),
                    "file_presence": file_presence,
                    "cluster_presence": cluster_presence_count,
                    "concentration_score": round(concentration, 4),
                    "source_mix": json.dumps(dict(item["source_counts"]), sort_keys=True),
                    "noise_labels": ", ".join(sorted(labels)),
                    "bundle_penalty": round(bundle_penalty, 4),
                    "adjusted_weight": round(adjusted_weight, 4),
                    "sample_paths": " || ".join(item["sample_paths"]),
                    "sample_files": " || ".join(item["sample_files"]),
                }
            )
        evidence_rows.sort(key=lambda row: row["adjusted_weight"], reverse=True)
        return evidence_rows

    return finalize_stats(token_stats), finalize_stats(phrase_stats), cluster_bundle_flags


def write_theme_evidence_csv(output_path, single_rows, phrase_rows):
    fieldnames = [
        "term",
        "token_type",
        "raw_count",
        "file_presence",
        "cluster_presence",
        "concentration_score",
        "source_mix",
        "noise_labels",
        "bundle_penalty",
        "adjusted_weight",
        "sample_paths",
        "sample_files",
    ]
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in single_rows + phrase_rows:
            writer.writerow(row)


def compute_modified_square_root_range(total_target_files):
    total_target_files = max(int(total_target_files or 0), 1)
    lower_bound = math.ceil(math.sqrt(total_target_files))
    upper_bound = math.floor(lower_bound * 1.5)
    return {
        "heuristic_name": "Modified Square Root Rule",
        "heuristic_basis": "Grounded in Heap's Law and Zipfian distributions.",
        "total_target_files": total_target_files,
        "target_id_count_min": lower_bound,
        "target_id_count_max": max(lower_bound, upper_bound),
    }


def _folder_key(path_value, depth=2):
    parts = [p for p in re.split(r"[\\/]+", path_value) if p]
    return "\\".join(parts[:-1][:depth]) or "."


def _spread(items, count):
    """`count` items spread evenly over the list: shows a folder's range, not just its first files."""
    if len(items) <= count:
        return list(items)
    return [items[i * len(items) // count] for i in range(count)]


def _group_summary(folder, group, samples):
    types = Counter((r.get("type") or "(none)").lower() for r in group)
    names = [f'{r.get("name", "")}{r.get("type", "")}' for r in group]
    return {"folder": folder, "files": len(group), "top_types": dict(types.most_common(3)), "sample_names": _spread(names, samples)}


def _folder_groups(rows, big, depth=2, max_depth=5):
    """Group files by folder (2 levels); a folder holding more than `big` files is split one level deeper, up to max_depth."""
    by_key = defaultdict(list)
    for r in rows:
        by_key[_folder_key(r["path"], depth)].append(r)
    out = {}
    for key, group in by_key.items():
        sub = _folder_groups(group, big, depth + 1, max_depth) if len(group) > big and depth < max_depth else None
        out.update(sub if sub and len(sub) > 1 else {key: group})
    return out


def summarize_scan(rows, max_folders=250, samples=5, max_types=40):
    """What an LLM needs to see: the size of the job, a folder map and a file-type table. rows = scan CSV dicts (path, name, type).
    Software folders and junk are counted but not mapped: fixed rules place them, so they need no bucket."""
    from johnny.core.planner import split_files  # pure stdlib, same rules the organize step applies first

    units, junk, routed = split_files(".", [r["path"] for r in rows])
    needing = set(routed)
    work = [r for r in rows if r["path"] in needing]
    groups = _folder_groups(work, big=max(300, len(work) // 30))
    types_by_folder = defaultdict(Counter)
    for key, group in groups.items():
        for r in group:
            types_by_folder[(r.get("type") or "(none)").lower()][key] += 1
    ranked = sorted(groups.items(), key=lambda item: -len(item[1]))
    listed = ranked[:max_folders]
    return {
        "drive_overview": {
            "files_total": len(rows),
            "software_units_handled_by_rule": {"count": len(units), "files": sum(units.values())},
            "junk_files_handled_by_rule": len(junk),
            "files_needing_a_bucket": len(routed),
            "folders_holding_them": len(groups),
            "unsorted_allowed": len(routed) // 150,
            "folders_listed": len(listed),
            "files_in_folders_not_listed": sum(len(g) for _, g in ranked[max_folders:]),
        },
        "folder_map": [_group_summary(folder, group, samples) for folder, group in listed],
        "file_type_table": [
            {"type": ext, "files": sum(c.values()), "main_folders": dict(c.most_common(2))}
            for ext, c in sorted(types_by_folder.items(), key=lambda item: -sum(item[1].values()))[:max_types]
        ],
    }


def group_leftovers(paths, nearest=None, max_groups=150, samples=5):
    """Files left in Unsorted, grouped by folder. `nearest(sample_paths)` supplies the closest buckets (it needs the model)."""
    groups = defaultdict(list)
    for p in paths:
        name, ext = os.path.splitext(os.path.basename(p))
        groups[_folder_key(p)].append({"path": p, "name": name, "type": ext.lower()})
    out = []
    for folder, group in sorted(groups.items(), key=lambda item: -len(item[1]))[:max_groups]:
        summary = _group_summary(folder, group, samples)
        if nearest:
            summary["closest_buckets"] = nearest(_spread([r["path"] for r in group], samples))
        out.append(summary)
    return out


def summarize_closest(candidate_lists, top=2):
    """Which buckets a group of files came closest to. candidate_lists = one [{category, score}, ...] per sample file."""
    closest = defaultdict(list)
    for candidates in candidate_lists:
        if candidates:
            closest[candidates[0]["category"]].append(candidates[0]["score"])
    ranked = sorted(closest.items(), key=lambda item: -len(item[1]))[:top]
    return [{"category": k, "samples_where_closest": len(v), "avg_score": round(sum(v) / len(v), 3)} for k, v in ranked]


def write_leftover_handoff_json(output_path, session_name, index_data, groups, unsorted_count, allowed, needing_a_bucket):
    """Round 2+ prompt for the LLM (pasted by hand): the current index and the files still in Unsorted, grouped by folder with the
    two nearest buckets. The reply is the complete updated master_index.json, so the normal index import path is reused."""
    handoff = {
        "session": session_name,
        "instructions": {
            "goal": (
                f"{unsorted_count} of {needing_a_bucket} files ended in Unsorted_Miscellaneous; at most {allowed} "
                "(1 in 150) are allowed. Improve current_index so these files get a bucket."
            ),
            "for_each_group_choose_one": [
                "add_to_existing_bucket: add aliases / positive_examples that use words from this group's folder path or sample names",
                "new_bucket: add a category (new code, no overlap with existing buckets) with a full profile",
                "handle_by_rule: junk, software or file pieces that no words can place; list them under notes.rules (not applied by the tool yet)",
            ],
            "requirements": [
                "The local matcher only reads each file's name and folder path as words, so every added word must appear in a folder path or sample name.",
                "closest_buckets shows where the matcher nearly put a group: a near miss usually means better words, no near miss means a new bucket.",
                "Keep existing codes and names unless you merge two buckets on purpose; say so in notes.",
                "Return the complete updated master_index.json (categories, incubation, profiles, optional notes).",
            ],
        },
        "current_index": {key: index_data.get(key, {}) for key in ("categories", "incubation", "profiles")},
        "leftover_groups": groups,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(handoff, f, indent=2)


def write_llm_handoff_json(
    output_path,
    session_name,
    single_rows,
    phrase_rows,
    cluster_bundle_flags,
    total_target_files,
    scan_rows=None,
    author_note="",
):
    id_target = compute_modified_square_root_range(total_target_files)
    handoff = {
        "session": session_name,
        "instructions": {
            "goal": "Create a human-readable Johnny.Decimal master_index.json from weighted evidence.",
            "requirements": [
                "Merge variants and aliases semantically.",
                "Use path hierarchy as evidence of purpose.",
                "Separate topical clusters from format/package noise.",
                "Keep categories broad enough to absorb real files but specific enough to reduce forced misclassification.",
                "Always include Unsorted_Miscellaneous.",
                (
                    "For every category except Unsorted_Miscellaneous, add a profile under a top-level \"profiles\" key: "
                    "a one-sentence description, 4-8 aliases (words a file name or path would contain), and 2-4 positive "
                    "example file names. Routing quality depends on these; without them a category is matched on its name only."
                ),
                "Avoid overfitting to one extracted software bundle.",
                "Read author_note and folder_map first: choose the organising principle the data supports and say why in notes.organising_principle.",
                "Choose the number of buckets yourself and give the reason in notes.bucket_count_reason; buckets must not overlap.",
                (
                    "The local matcher only reads each file's name and folder path as words, so write aliases and examples "
                    "with words that really appear in folder_map paths and sample names."
                ),
                "Success test: at most unsorted_allowed files (1 in 150 of files_needing_a_bucket) may end in Unsorted_Miscellaneous.",
                (
                    f"For reference only: the Modified Square Root Rule suggests {id_target['target_id_count_min']} to "
                    f"{id_target['target_id_count_max']} specific subcategories (IDs) for this file count. "
                    "You decide the number the data supports."
                ),
            ],
            "output_contract": {
                "format": "master_index.json",
                "required_keys": ["categories", "incubation"],
                "optional_keys": {
                    "profiles": {"<Category_Name>": {"description": "...", "aliases": ["..."], "positive_examples": ["..."]}},
                    "notes": {"organising_principle": "...", "bucket_count_reason": "..."},
                },
            },
        },
        "taxonomy_target": id_target,
        "top_single_terms": single_rows[:120],
        "top_phrases": phrase_rows[:120],
        "bundle_like_clusters": [
            {"cluster_root": key, **value}
            for key, value in cluster_bundle_flags.items()
            if value.get("is_bundle_like")
        ],
    }
    if scan_rows is not None:
        handoff.update(summarize_scan(scan_rows))
        handoff["author_note"] = author_note or "(none: add a few lines about what this drive is for to author_note.txt next to drive_scan.csv, then re-run Create Plan)"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(handoff, f, indent=2)


def read_theme_evidence_csv(evidence_path):
    with open(evidence_path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))
