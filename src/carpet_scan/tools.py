"""Run layer: one subprocess or API call per tool. No parsing, no analysis."""

from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

from carpet_scan.model import VULTURE_MIN_CONFIDENCE, ToolVersions


class ToolFailure(RuntimeError):
    """A tool could not produce usable output. Carries its stderr so the cause is visible."""


def _capture(command: list[str], *, tolerate: tuple[int, ...] = (0, 1, 3)) -> str:
    """Run a tool and return stdout.

    Linters exit non-zero when they have findings, so a non-zero code is not by itself a failure —
    hence ``tolerate``. Empty stdout is treated as failure by the callers that need output.
    """
    logger.debug(f"running: {' '.join(command)}")
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode not in tolerate:
        raise ToolFailure(f"{command[0]} exited {result.returncode}: {result.stderr[-600:]}")
    return result.stdout


def _tool(*args: str) -> list[str]:
    """Tools are taken from ``PATH``.

    They are dependencies of this package, so any environment that can run this script has them
    installed — no package-manager invocation is baked in, which keeps the tool usable in projects
    that do not use uv.
    """
    return list(args)


def run_pyan(src: Path, tests: Path | None, out: Path) -> str:
    """Static name-level `uses` graph as DOT.

    ``--root .`` is mandatory: without it pyan3 cannot infer module names under ``src`` and warns
    that the inferred names will be wrong.
    """
    targets = [str(src), *([str(tests)] if tests else [])]
    command = _tool("pyan3", *targets, "--root", ".", "--uses", "--no-defines", "--dot")
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if not result.stdout.strip():
        # pyan3 is fragile — it raises on some valid constructs, e.g. a lambda inside a list
        # comprehension inside a function. Surface its own message rather than a parse error.
        raise ToolFailure(f"pyan3 produced no graph: {result.stderr.strip()[-600:]}")
    out.write_text(result.stdout, encoding="utf-8")
    return result.stdout


def run_vulture(src: Path, tests: Path | None, ignored_decorators: str) -> str:
    """Dead code. Decorator-aware, so runtime-registered entry points are not reported."""
    targets = [str(src), *([str(tests)] if tests else [])]
    return _capture(
        _tool(
            "vulture",
            *targets,
            "--min-confidence",
            str(VULTURE_MIN_CONFIDENCE),
            "--ignore-decorators",
            ignored_decorators,
        )
    )


def run_radon(src: Path, metric: str) -> str:
    """`radon cc` or `radon mi` as JSON."""
    return _capture(_tool("radon", metric, str(src), "-j"))


def run_ruff_private(src: Path, tests: Path | None) -> str:
    """Cross-module private access, as JSON.

    Two rules, one per form: ``SLF001`` catches ``other._private(...)`` and ``PLC2701`` catches
    ``from other import _private``. ``--preview`` is required — ``PLC2701`` is preview-gated, and
    without the flag Ruff prints "Selection PLC2701 has no effect" and reports nothing.

    The project's own Ruff configuration still applies, so a ``per-file-ignores`` entry excusing
    these rules is respected. That is deliberate: the count then measures what policy does *not*
    already excuse.
    """
    targets = [str(src), *([str(tests)] if tests else [])]
    return _capture(
        _tool(
            "ruff",
            "check",
            *targets,
            "--select",
            "SLF001,PLC2701",
            "--preview",
            "--output-format",
            "json",
            "--no-cache",
        )
    )


def infer_package(src: Path) -> str:
    """The single importable package under ``src``.

    Raises when there is not exactly one, rather than guessing: grimp needs a real package name
    and a wrong guess produces a silently empty folder table.
    """
    candidates = sorted(
        child.name
        for child in src.iterdir()
        if child.is_dir() and not child.name.startswith((".", "_")) and child.name != "__pycache__"
    )
    if len(candidates) != 1:
        raise RuntimeError(
            f"cannot infer the package name from {src}: found {candidates or 'nothing'}. "
            "Pass --package explicitly."
        )
    return candidates[0]


def tool_versions() -> ToolVersions:
    def version(package: str) -> str:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as read

        try:
            return read(package)
        except PackageNotFoundError:
            return "unknown"

    return ToolVersions(
        pyan3=version("pyan3"),
        vulture=version("vulture"),
        radon=version("radon"),
        grimp=version("grimp"),
        ruff=version("ruff"),
    )
