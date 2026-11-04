# carpet_scan — agent rules

The closest `AGENTS.md` wins. Explicit user instructions override this file.

## Verified commands

| Task | Command |
| --- | --- |
| Synchronize | `uv sync --frozen --group dev` |
| Format | `.venv/bin/ruff format src tests && .venv/bin/ruff check --fix src tests` |
| Lint | `.venv/bin/ruff check src tests` |
| Typecheck | `.venv/bin/pyright` |
| Dependencies | `.venv/bin/deptry .` |
| Tests | `.venv/bin/pytest --cov --cov-branch` |
| Build | `uv build && .venv/bin/twine check dist/*` |
| Self-scan | `make carpets` — this package, by itself |
| Full gate | `make check` |

## Code conventions

- Fully annotate every function and method in `src/` and `tests/`, including
  private functions, callbacks, generators, fixtures, and special methods.
- Standard Pyright strict and Ruff are mandatory. Do not create baselines,
  blanket exclusions, file-wide ignores, or unqualified `# type: ignore`.
- Ruff is the sole formatter and linter. Do not add Black, isort, Flake8, mypy,
  or another overlapping formatter/type checker.
- Keep `__init__.py` empty and import public objects from their defining modules.
- Use Google-style docstrings for public APIs and the configured 100-character
  line length.

## Dependency rules

### Required

- Declare every imported runtime dependency directly in `[project.dependencies]`.
- Put tests, linting, typing, building, and documentation tools in dependency
  groups; optional user-facing capabilities belong in extras.
- Update `pyproject.toml` and `uv.lock` together and run `make check`.

### Recommended

- Prefer the standard library, then an existing direct dependency, then a small,
  maintained, typed dependency.
- Keep source independent of test, build, documentation, and CLI-only packages.

### Forbidden

- Depend on unpinned branches or undeclared transitive dependencies.
- Add parallel manifests, lockfiles, formatters, type checkers, or test runners.
- Silence a dependency or typing defect instead of fixing its source.

## Workflow

1. Preserve unrelated worktree changes.
2. Add or update focused tests with each behavioral change.
3. Run the smallest relevant check while iterating.
4. Run `make check` before handoff and report its actual result.

## Evidence and reports

- `.carpet/runs/*/evidence.zip` is immutable producer evidence. Never overwrite an archive or
  reconstruct its raw contents from normalized rows.
- SQLite contains queryable producer-shaped evidence. Report thresholds and derived judgements do
  not belong in the evidence tables.
- Report code reads stored evidence and must not launch tools, import live scanned source, or use a
  stored metric to choose an exit status.
- A local dirty run records that it is dirty but never archives its source or diff.
- Run `marimo check src/carpet_scan/notebooks/history_report.py` after changing the report program,
  then verify its emitted HTML contains no CDN/runtime dependency.
