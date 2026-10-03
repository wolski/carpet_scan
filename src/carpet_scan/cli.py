"""Capture carpet evidence and render non-blocking code-shape reports."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import cyclopts
from loguru import logger

from carpet_scan.derived import DerivedReport, derive_report
from carpet_scan.evidence import (
    SealedRun,
    TargetRecord,
    capture_evidence,
    default_config,
    seal_evidence,
)
from carpet_scan.history import ingest_archive, latest_run_id, load_snapshot
from carpet_scan.model import IGNORED_DECORATORS, SURFACE_SKIP, ScanConfig
from carpet_scan.report import (
    render_cohesion_markdown,
    render_html,
    render_markdown,
    render_terminal,
)
from carpet_scan.targets import (
    ProjectLayout,
    github_target,
    local_target,
    project_layout,
)
from carpet_scan.tools import infer_package

app = cyclopts.App(name="carpet-scan", help=__doc__)


@dataclass
class Tuning:
    """Reporting thresholds and per-project calibration."""

    warn_depth: int = 3
    """Intra-module call depth at or above which a module is listed."""

    top: int = 12
    """Rows per terminal table. The report always lists every row."""

    surface_skip: tuple[str, ...] = SURFACE_SKIP
    """Subpackages omitted from the public-surface derivation."""

    ignore_decorators: str = IGNORED_DECORATORS
    """Comma-separated decorators whose functions vulture must not report."""


@dataclass(frozen=True)
class Acquisition:
    """Published and normalized evidence from one project scan."""

    database: Path
    sealed: SealedRun
    derived: DerivedReport


def _configuration(layout: ProjectLayout, tuning: Tuning) -> ScanConfig:
    return replace(
        default_config(layout),
        top=tuning.top,
        warn_depth=tuning.warn_depth,
        surface_skip=tuning.surface_skip,
        ignore_decorators=tuning.ignore_decorators,
    )


def _acquire(
    layout: ProjectLayout,
    target: TargetRecord,
    carpet_root: Path,
    config: ScanConfig,
) -> Acquisition:
    started = datetime.now(UTC)
    logger.info(f"capturing diagnostic evidence for {target.project_identity}")
    producers = capture_evidence(layout, config)
    sealed = seal_evidence(carpet_root, started, target, config, producers)
    database = carpet_root / "history.sqlite3"
    run_id = ingest_archive(database, sealed.archive)
    snapshot = load_snapshot(database, run_id)
    derived = derive_report(snapshot)
    statuses = ", ".join(f"{item.producer}={item.status}" for item in producers)
    logger.info(f"evidence run {run_id}: {statuses}")
    logger.info(f"archive: {sealed.archive}")
    logger.info(f"history: {database}")
    return Acquisition(database=database, sealed=sealed, derived=derived)


def _write_legacy_reports(
    acquisition: Acquisition,
    report: Path,
    cohesion: Path,
    html: Path | None,
) -> None:
    derived = acquisition.derived
    render_terminal(
        derived.surface,
        derived.depth,
        derived.folders,
        derived.dead,
        derived.private,
        derived.cohesion,
        derived.config,
    )
    markdown = render_markdown(
        derived.surface,
        derived.depth,
        derived.folders,
        derived.dead,
        derived.private,
        derived.config,
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(markdown, encoding="utf-8")
    cohesion.parent.mkdir(parents=True, exist_ok=True)
    cohesion.write_text(
        render_cohesion_markdown(derived.cohesion, derived.config), encoding="utf-8"
    )
    if html is not None:
        html.parent.mkdir(parents=True, exist_ok=True)
        html.write_text(render_html(markdown, derived.config), encoding="utf-8")
    logger.info(
        f"compatibility reports: {report}, {cohesion}" + (f", {html}" if html is not None else "")
    )


@app.default
def scan(
    src: Path,
    *,
    tests: Path | None = None,
    package: str | None = None,
    report: Path = Path("build/carpet-report.md"),
    cohesion: Path = Path("build/cohesion-report.md"),
    html: Path | None = None,
    tuning: Annotated[Tuning, cyclopts.Parameter(name="*")] | None = None,
) -> None:
    """Scan the current project, store its evidence, and write compatibility reports.

    This default command preserves the original ``carpet-scan src --tests tests`` interface.
    New automation should prefer ``scan-folder`` or ``scan-github`` so the target is explicit.
    Diagnostic values never affect this command's exit status.
    """
    project = Path.cwd().resolve()
    selected_package = package or infer_package(src)
    layout = ProjectLayout(
        project=project,
        src=src.resolve(),
        tests=tests.resolve() if tests is not None else None,
        package=selected_package,
    )
    tuning = tuning or Tuning()
    target = local_target(project)
    acquisition = _acquire(
        layout,
        TargetRecord(
            kind="folder",
            project_identity=f"folder:{project}",
            project_path=str(project),
            repository_url=None,
            commit_sha=None,
            head_sha=target.head_sha,
            dirty=target.dirty,
        ),
        project / ".carpet",
        _configuration(layout, tuning),
    )
    _write_legacy_reports(acquisition, report, cohesion, html)


@app.command
def scan_folder(
    project: Path,
    *,
    tuning: Annotated[Tuning, cyclopts.Parameter(name="*")] | None = None,
) -> None:
    """Capture one local project folder, including its current uncommitted state."""
    target = local_target(project)
    layout = project_layout(target.project)
    acquisition = _acquire(
        layout,
        TargetRecord(
            kind="folder",
            project_identity=f"folder:{target.project}",
            project_path=str(target.project),
            repository_url=None,
            commit_sha=None,
            head_sha=target.head_sha,
            dirty=target.dirty,
        ),
        target.project / ".carpet",
        _configuration(layout, tuning or Tuning()),
    )
    _write_legacy_reports(
        acquisition,
        acquisition.sealed.directory / "carpet-report.md",
        acquisition.sealed.directory / "cohesion-report.md",
        None,
    )


@app.command
def scan_github(
    repository_url: str,
    commit: str,
    *,
    workdir: Path,
    tuning: Annotated[Tuning, cyclopts.Parameter(name="*")] | None = None,
) -> None:
    """Capture one exact GitHub revision in a reusable managed project folder."""
    with github_target(repository_url, commit, workdir) as target:
        layout = project_layout(target.checkout)
        acquisition = _acquire(
            layout,
            TargetRecord(
                kind="github",
                project_identity=target.repository_url,
                project_path=str(target.checkout),
                repository_url=target.repository_url,
                commit_sha=target.commit_sha,
                head_sha=target.commit_sha,
                dirty=False,
            ),
            target.managed_project / ".carpet",
            _configuration(layout, tuning or Tuning()),
        )
        _write_legacy_reports(
            acquisition,
            acquisition.sealed.directory / "carpet-report.md",
            acquisition.sealed.directory / "cohesion-report.md",
            None,
        )


@app.command
def ingest(database: Path, archive: Path) -> None:
    """Rebuild one normalized run from an immutable evidence ZIP."""
    run_id = ingest_archive(database, archive)
    logger.info(f"ingested evidence run {run_id} into {database}")


@app.command
def report(
    database: Path,
    *,
    run_id: str | None = None,
    output: Path = Path("carpet-report.html"),
) -> None:
    """Export one stored evidence run as self-contained marimo HTML."""
    selected = run_id or latest_run_id(database)
    output.parent.mkdir(parents=True, exist_ok=True)
    notebook = Path(__file__).parent / "notebooks" / "history_report.py"
    command = [
        sys.executable,
        str(notebook),
        "--database",
        str(database.resolve()),
        "--run-id",
        selected,
        "--output",
        str(output.resolve()),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"marimo report failed: {result.stderr[-1000:]}")
    logger.info(f"rendered stored run {selected}: {output}")


if __name__ == "__main__":
    app()
