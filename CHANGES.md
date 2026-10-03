# Changes

- 2026-08-28: Added the missing `LICENSE` file (MIT) and declared `license-files`, without which
  uv_build shipped wheels carrying no licence text.

- 2026-08-13: Separate evidence acquisition from reporting. Local folders and exact GitHub commits
  now produce checksummed raw ZIP archives and producer-native SQLite rows for pyan3, Vulture,
  Radon, Ruff, Grimp, and NetworkX. Archives can rebuild normalized history without rerunning tools.
  A marimo report program renders stored runs as offline self-contained HTML with source locations,
  a dependency matrix, and call-chain diagrams. Display thresholds remain representation-only and
  diagnostic values still never affect exit status.
- 2026-08-13: Add Check 6, folder cohesion, written to its own `build/cohesion-report.md`. Reuses
  the grimp graph Check 5 already builds and adds one networkx Louvain pass: per-folder
  internal-vs-external edge share, plus the modularity of the declared folder partition against the
  communities the graph itself has. Kept a separate document because it is a packaging question
  rather than the per-module one, and the report says in as many words that a layered design scores
  low by construction — the actionable finding is a folder split across clusters, not a low ratio.
  Seeded so unchanged code yields an identical report. The measurement takes plain module names and
  edges, so it is tested without an importable package on disk.
- 2026-08-11: Add `make carpets` so the package scans itself. Clean on every check: 21 modules, no
  public function without an external caller, nothing at call depth >= 3, no dead code, no
  cross-module private access, worst folder import depth 2.
- 2026-08-11: Lower `requires-python` from 3.13 to 3.12, with Ruff `target-version` and Pyright
  `pythonVersion` to match. PEP 695 `type` aliases are the newest syntax used here, so 3.12 is the
  real floor — and the tool installs into the environment of whatever project it scans, where a high
  floor makes it unusable (Prozor is 3.12).
- 2026-08-11: Resolve the aggregated tools beside `sys.executable` before falling back to `PATH`.
  Invoking the console script by absolute path (`.venv/bin/carpet-scan`, the idiom in the sibling
  Makefiles) does not put that `bin` on `PATH`, and the scan died with `FileNotFoundError: 'pyan3'`.
- 2026-08-11: Add cross-module private access as a fifth tool (Ruff `SLF001`/`PLC2701`, tracker
  Check 1), optional `--html` rendering, per-table captions carrying column meanings and the exact
  generating command, and tests for the pure layers (23 tests, 68% coverage).
- 2026-08-11: Extract from `apb/scripts/carpet_scan.py` into a standalone package: an aggregator over
  pyan3, vulture, radon and grimp that performs no analysis of its own.
