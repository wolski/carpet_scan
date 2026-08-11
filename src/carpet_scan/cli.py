"""Command-line entry point. Orders the four stages and nothing else."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import cyclopts
from loguru import logger

from carpet_scan.measure import depth_rows, folder_rows, surface_rows
from carpet_scan.model import IGNORED_DECORATORS, SURFACE_SKIP, ScanConfig, TreeLayout
from carpet_scan.parsing import parse_pyan, parse_radon_cc, parse_radon_mi, parse_vulture
from carpet_scan.report import render_markdown, render_terminal
from carpet_scan.tools import (
    ToolFailure,
    infer_package,
    run_pyan,
    run_radon,
    run_vulture,
    tool_versions,
)

app = cyclopts.App(name="carpet-scan", help=__doc__)


@dataclass
class Tuning:
    """Reporting thresholds and per-project calibration.

    Flattened onto the command line by cyclopts, so these stay plain ``--warn-depth`` style
    options while keeping the entry point's signature small.
    """

    warn_depth: int = 3
    """Intra-module call depth at or above which a module is listed."""

    top: int = 12
    """Rows per terminal table. The report always lists every row."""

    surface_skip: tuple[str, ...] = SURFACE_SKIP
    """Subpackages omitted from Check 2: their publics are decorator-registered entry points,
    which a uses-graph cannot see."""

    ignore_decorators: str = IGNORED_DECORATORS
    """Comma-separated decorators whose functions vulture must not report. Defaults suit pydantic
    and cyclopts; Click, FastAPI or Celery projects need their own."""


@app.default
def scan(
    src: Path,
    *,
    tests: Path | None = None,
    package: str | None = None,
    report: Path = Path("build/carpet-report.md"),
    tuning: Annotated[Tuning, cyclopts.Parameter(name="*")] | None = None,
) -> None:
    """Aggregate pyan3, vulture, radon and grimp into carpet diagnostics. Always exits 0.

    Parameters
    ----------
    src
        Source root, e.g. ``src`` or ``lib``.
    tests
        Test root. Included in the graph so "referenced only from tests" is distinguishable.
    package
        Importable package name, for grimp's module graph. Inferred from ``src`` when it holds
        exactly one package directory.
    report
        Markdown report path, holding the detail the terminal tables omit.
    """
    tuning = tuning or Tuning()
    package = package or infer_package(src)
    report.parent.mkdir(parents=True, exist_ok=True)
    logger.info("running pyan3, vulture, radon, grimp")

    try:
        nodes, edges = parse_pyan(run_pyan(src, tests, report.parent / "pyan.dot"))
    except ToolFailure as failure:
        logger.error(f"{failure}")
        logger.warning("skipping the public-surface and call-depth checks; the rest still run")
        nodes, edges = {}, []
    dead = parse_vulture(run_vulture(src, tests, tuning.ignore_decorators))
    complexity = parse_radon_cc(run_radon(src, "cc"))
    maintainability = parse_radon_mi(run_radon(src, "mi"))

    layout = TreeLayout.of(src, tests)
    dead_keys = {(f.path.resolve(), f.name) for f in dead}
    surface, skipped = surface_rows(
        nodes,
        edges,
        dead_keys,
        complexity,
        maintainability,
        frozenset(tuning.surface_skip),
        layout,
    )
    depth = depth_rows(nodes, edges, complexity, tuning.warn_depth, layout)
    folders = folder_rows(package)

    config = ScanConfig(
        src=src,
        tests=tests,
        package=package,
        top=tuning.top,
        warn_depth=tuning.warn_depth,
        modules=len({n.module for n in nodes.values()}),
        versions=tool_versions(),
        surface_skip=tuning.surface_skip,
        skipped=skipped,
        ignore_decorators=tuning.ignore_decorators,
    )

    render_terminal(surface, depth, folders, dead, config)
    report.write_text(render_markdown(surface, depth, folders, dead, config), encoding="utf-8")

    home = sum(len(r.home_only) for r in surface)
    gone = sum(len(r.unused) for r in surface)
    logger.info(
        f"{config.modules} modules | {home} publics used only at home, {gone} confirmed dead "
        f"| {len(depth)} at call depth >= {tuning.warn_depth} | {len(dead)} vulture findings"
    )
    logger.info(f"full report: {report} — flags are for triage, never act on a number alone")


if __name__ == "__main__":
    app()
