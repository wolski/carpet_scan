# carpet_scan

Carpet diagnostics capture code-shape evidence from pyan3, Vulture, Radon, Ruff, Grimp, and
NetworkX. Acquisition and reporting are separate: the diagnostic tools run once, their exact output
is archived and normalized into SQLite, and reports are regenerated from stored evidence without
touching the source or rerunning a tool.

Every number is a gauge for human triage. No metric affects an exit status.

## Capture a project

Run against a local folder, including its current uncommitted state:

```bash
uvx carpet-scan scan-folder /path/to/project
```

Run against an exact GitHub revision:

```bash
uvx carpet-scan scan-github \
  https://github.com/pypa/sampleproject.git \
  621e4974ca25ce531773def586ba3ed8e736b3fc \
  --workdir /path/to/carpet-projects
```

GitHub acquisition creates or reuses `<workdir>/<org>_<project>/`, fetches the requested commit into
an isolated detached worktree, and removes that checkout after evidence capture. Its `.carpet/`
history remains in the common project directory. The requested project needs `pyproject.toml`; no
lockfile is required.

The original in-project interface remains available:

```bash
uv run carpet-scan src --tests tests --html build/carpet-report.html
```

It now records evidence in `.carpet/` before writing the compatibility Markdown/HTML reports.

## Evidence layout

```text
<project>/.carpet/
├── history.sqlite3
└── runs/
    └── <UTC-datetime>_<commit-or-none>/
        ├── evidence.zip
        ├── carpet-report.md
        ├── cohesion-report.md
        └── report.html
```

`evidence.zip` is the preservation boundary. It holds the original DOT, JSON, and text outputs plus
a manifest containing commands/API operations, tool versions, source identity, completion status,
and SHA-256 checksums. The scanned source, a dirty Git diff, a target lockfile, and generated reports
are not archived.

`history.sqlite3` is the query boundary. Schema version 1 stores producer-shaped normalized data:
pyan nodes/uses-edges, Vulture findings, complete nested Radon blocks and MI rows, Ruff findings and
fix edits, Grimp modules/import-edges, and the exact seeded NetworkX community partition. Display
thresholds never limit stored evidence.

A dirty local run uses `_none` in its run directory. Its evidence is preserved, but carpet does not
claim that its uncommitted source can be reconstructed later.

## Render from stored evidence

```bash
uvx carpet-scan report /path/to/project/.carpet/history.sqlite3 \
  --run-id <run-id> \
  --output report.html
```

The marimo report program reads only SQLite evidence and emits compact self-contained HTML with no
CDN, server, Python process, or database connection needed after creation. It includes the existing
carpet/cohesion sections, an evidence-backed dependency matrix, stored call-chain diagrams, and a
source index. GitHub runs link to immutable `blob/<full-commit>/...#L...` locations; local runs show
copyable repository-relative paths and lines.

Omit `--run-id` to select the latest recorded run.

## Recover normalized history

The raw archive is authoritative. Rebuild a deleted or new SQLite database without rerunning tools:

```bash
uvx carpet-scan ingest /path/to/history.sqlite3 /path/to/evidence.zip
```

Ingestion verifies ZIP integrity and every manifest checksum, then inserts the run and all producer
evidence in one transaction. Unsupported archive or database schema versions fail without rewriting
the data.

## Development

```bash
uv sync --group dev
make check
.venv/bin/marimo check src/carpet_scan/notebooks/history_report.py
.venv/bin/pre-commit install --hook-type pre-commit --hook-type pre-push
```

All Python commands run from the synchronized project `.venv`. `make clean` intentionally leaves
`.carpet/` untouched.
