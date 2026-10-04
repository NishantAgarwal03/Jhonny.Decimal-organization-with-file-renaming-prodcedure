# Architecture

## System overview

Johnny Organizer is a local, safety-first pipeline in four parts: scan a folder, rename weak filenames, build a category plan, then route files into Johnny.Decimal (`NN.NN_Category`) folders. Everything runs locally; the only external step (authoring `master_index.json`) is done manually from a generated handoff file. The UI is `johnny/ui/jd_ui_prototype.py` (Tkinter); `johnny/organize/organizer.py` and `johnny/rename/rename_files.py` are CLI equivalents.

## Repository layout

| Folder | What it holds | Tracked |
|---|---|---|
| (root) | `README.md`, this file, `requirements.txt`, config (`routing_calibration.json`, `theme_noise_config.json`, `master_index.example.json`) | yes |
| `johnny/` | All the Python code, split by role (see the package table below). Run with `python -m johnny.<pkg>.<module>` from the repo root | yes |
| `runs/session_<name>/` | One run per scanned drive or folder: scan CSV, analysis, handoff, index. Generated. | no |
| `data/inputs/` | Hand-made or exported inputs: labelled filename sets, scan inventories | no |
| `data/results/` | Generated outputs of benchmarks and analyses | no |
| `tests/`, `fixtures/` | Regression tests and the small routing benchmark data | yes |
| `docs/` | `BENCHMARKS.md`, `assets/` (images), `benchmarks/` (published results), `project/` (status, tasks, checkpoint, audit, problem statement) | yes |
| `archive/` | `scratch/` old scripts, `_trash_<date>/` items set aside for deletion | partly |
| `master_index.json` | Your real category index (local only; copy `master_index.example.json`) | no |

### The `johnny/` package

| Package | Role | Modules |
|---|---|---|
| `johnny/core/` | Shared building blocks: safe rename + undo log, planner rules, index, embeddings, NLP, evidence | `utils`, `planner`, `index_manager`, `embedding_manager`, `nlp_engine`, `evidence_engine` |
| `johnny/scan/` | Step 1: inventory a drive and analyse it | `scan_drive`, `drive_context_analyzer`, `fast_seed_csv`, `generate_tfidf_themes` |
| `johnny/rename/` | Step 2: Quick rename (rules) and Deep rename (NLP) | `filename_standardizer`, `rename_engine`, `rename_files` |
| `johnny/organize/` | Steps 3-4: route and move files (CLI) | `router`, `organizer` |
| `johnny/ui/` | The Tkinter app that drives all steps | `jd_ui_prototype` |
| `johnny/tools/` | Benchmarks, preflight, backup identity index | `jd_benchmark`, `backup_identity_index` |

Config files stay in the repo root because the code locates them via the repo root (`PROJECT_DIR`).

## Data flow

```text
target folder
  ↓ scan (johnny/scan/scan_drive.py / UI "Scan")
runs/session_<name>/drive_scan.csv   (path,name,type)
  ↓
  ├─ Quick rename: johnny/rename/filename_standardizer.py   (regex rules, no model)
  ├─ Deep rename:  johnny/rename/rename_engine.py → johnny/core/nlp_engine.py → johnny/core/embedding_manager.py
  ↓
johnny/scan/drive_context_analyzer.py → johnny/core/evidence_engine.py
  ↓
mapping_audit.csv, top_drive_theme_evidence.csv, llm_master_index_handoff.json
  ↓ (manual: the taxonomy is authored from the handoff)
master_index.json  {"categories": {name: "NN.NN"}, "incubation": {}}
  ↓
nlp_engine.get_topic_details  (embedding match + abstention → Unsorted_Miscellaneous)
  ↓ johnny/core/planner.py splits files first: software units (whole folders), junk (safe / review), routed
  ↓ 1/150 unsorted guard (routed files only)
johnny/organize/router.py → <root>/NN.NN_Category/<file>   (units → _Software_Projects, junk → _Temp_Safe_To_Remove / _Temp_Review_First)
```

Related but separate: `johnny/scan/fast_seed_csv.py` batch-labels a scan CSV against an index and writes `mapping_audit_v2.csv` plus a validation summary. `johnny/tools/backup_identity_index.py` tracks files by Windows file ID in SQLite and is unrelated to sorting.

## Module boundaries

| Module | Role | Depends on |
|---|---|---|
| `johnny/rename/filename_standardizer.py` | Quick rename: regex tables, gates, meaning-preservation check | `utils` |
| `johnny/rename/rename_engine.py` | Deep rename for vague names; topic + ranked terms from text/path | `nlp_engine`, `index_manager`, `utils` |
| `johnny/core/nlp_engine.py` | Text extraction (txt/pdf/docx), category prototypes, routing decision | `embedding_manager` |
| `johnny/core/embedding_manager.py` | Singleton sentence-transformer loader, E5 `query:`/`passage:` prefixes, calibration + profile loaders | `sentence-transformers` |
| `johnny/core/evidence_engine.py` | Token evidence, weighting, taxonomy-size heuristic, handoff JSON | none |
| `johnny/scan/drive_context_analyzer.py` | CLI driver for evidence generation | `evidence_engine` |
| `johnny/core/index_manager.py` | Read/write `master_index.json`, mint JD codes | none |
| `johnny/core/planner.py` | Rule pre-pass: junk tiers, software/environment units, managed folder names | none |
| `johnny/organize/router.py` | Move a file (or a whole unit) into its folder, collision-safe | `index_manager`, `planner`, `utils` |
| `johnny/organize/organizer.py` | CLI: route + rename a folder, with the 1/150 guard | the above |
| `johnny/ui/jd_ui_prototype.py` | Tkinter UI over all of the above; has its own copy of the organize plan logic | the above |
| `johnny/tools/jd_benchmark.py` | Labelled-fixture benchmark and threshold grid search | `nlp_engine` |

The UI and `johnny/organize/organizer.py` build their plans separately and differ. The UI embeds filename + folder path only. `johnny/organize/organizer.py` embeds extracted document text unless `--fast-seed` is set.

## Do not change

- Quick rename must keep skipping ambiguous names (high precision and skip safety is the design goal).
- The `1/150` unsorted guard blocks apply: `allowed = floor(N/150)`.
- Category keys in `master_index.json` must match the keys the NLP engine and category profiles use.

## Decisions

### Rules before routing

- **Decision:** Before semantic routing, `johnny/core/planner.py` separates software/environment trees (moved whole as one unit to `_Software_Projects`) and junk (`_Temp_Safe_To_Remove` for regenerable files, `_Temp_Review_First` for backups, logs and partial downloads). Only the remaining files are routed and counted by the 1/150 guard.
- **Alternatives considered:** Route every file individually (the interior of a virtual environment then floods Unsorted); delete junk (not safe: a `.bak` can be the only copy).
- **Why this one:** The guard exists to detect a failing index. Files handled by fixed rules are not routing failures, so counting them would block a good index and dilute a bad one.
- **Constraints this creates:** The special folders start with `_` and are never planned again on a re-run; a folder with a `.git`, `node_modules` or `site-packages` marker moves as a whole, so its project files travel with it.

### Abstain instead of forcing a category

- **Decision:** Route to `Unsorted_Miscellaneous` when top-1 confidence or the top-1/top-2 margin is below calibrated thresholds.
- **Alternatives considered:** Always assign the top-1 category.
- **Why this one:** A wrong folder is harder to notice than an unsorted one.
- **Constraints this creates:** The 1/150 guard blocks apply when too many files abstain. Weak input text (filename and path only) makes this block likely.

### Conservative filename-only first pass

- **Decision:** Quick rename applies the smallest safe transformation and skips when unsure.
- **Alternatives considered:** Aggressive rebuilding of every name.
- **Why this one:** Renames are not undoable.
- **Constraints this creates:** Recall is low (0.34 on the 1000-case benchmark) by design.

### Manual taxonomy-authoring step

- **Decision:** The code produces an evidence handoff; a person writes `master_index.json`.
- **Alternatives considered:** Automatic clustering into categories.
- **Why this one:** Category names need human review.
- **Constraints this creates:** The handoff is a prompt, so it must stay readable. The current dynamic taxonomy size is `ceil(sqrt(N))` to `floor(1.5 × ceil(sqrt(N)))` categories.
