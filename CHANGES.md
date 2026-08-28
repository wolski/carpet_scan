# Changes

- 2026-08-28: Added the missing `LICENSE` file (MIT) and declared `license-files`, without which
  uv_build shipped wheels carrying no licence text.

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
