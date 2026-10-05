# Synthetic drive (the judge)

Made-up names only, no personal data. Regenerate with `python tests/make_synthetic_drive.py` (deterministic).

| File | What it is |
|---|---|
| `synthetic_scan.csv` | What the tool sees: path, name, type for 3,455 files (like `drive_scan.csv`). |
| `synthetic_truth.csv` | **The judge**: the same files fully sorted as they should be (path, bucket, tag). `Unsorted_Miscellaneous` means the name and path hold no usable information, so refusing is correct. `_Software_Projects` / `_Temp_Safe_To_Remove` are files the fixed rules must take out first. |
| `index_round1.json` | A stand-in for the LLM's first bucket list, written from the round 1 handoff. |
| `index_round2.json` | The same list after the leftover handoff: added artist and package words, removed generic ones. |
| `index_bucket_map.json` | Maps the bucket names the indexes use to the key's names, so the judge and the LLM do not share vocabulary. |
| `baseline.json` | Last accepted score of the default setting per index. |

Run the judge (loads the model, about a minute the first time, seconds after): `python tests/synthetic_benchmark.py [--index index_round2.json]`.

Hard situations (the `tag` column): `clean`, `clean-generic` (camera names, the folder says what they are), `small-clean` (folders too small to vote),
`installer-package` (software packages holding documents and scripts), `dump-descriptive` (a mixed Downloads folder),
`unknowable` (names such as `scan0001.pdf`), `decoy-host` / `decoy-minority` (a minority of files in the wrong kind of folder),
`overlap` (topics that fit two buckets), `deep-clean-children` (mixed parent, clean sub-folders), `transliterated`, `rule-unit`, `rule-junk`.

Scoring per file: **correct**, **wrong** (a wrong bucket: the costly mistake) or **missed** (left in Unsorted although a right bucket exists).
The synthetic set must stay a judge: do not tune settings until this set scores well; use it to see the trade-offs, and add a second seed as a hold-out before trusting a tuned number.
