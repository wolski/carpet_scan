"""Typed rows. Every field traces to a tool's output; nothing here measures anything."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

MIN_PUBLICS_FOR_SURFACE_REPORT = 4
MIN_NAMES_SHARING_A_MODIFIER = 3
FOLDER_DEPTH_WARN = 3
VULTURE_MIN_CONFIDENCE = 60
CALLABLE_KINDS = frozenset({"function", "method", "classmethod"})
# Defaults cover pydantic validators and cyclopts commands. Projects on Click, FastAPI or
# Celery need their own set — pass --ignore-decorators.
IGNORED_DECORATORS = "@field_validator,@model_validator,@app.command,@app.default,@property"
# CLI entry points are registered by decorator, which a uses-graph cannot see, so every
# command in these subpackages is reported as unreferenced on every run. Vulture still
# covers them in the dead-code section, where decorator awareness is built in.
SURFACE_SKIP = ("scripts",)


@dataclass(frozen=True)
class TreeLayout:
    """The pyan3 module-name prefixes for the roots that were scanned."""

    source: str
    tests: str | None

    @classmethod
    def of(cls, src: Path, tests: Path | None) -> TreeLayout:
        return cls(source=src.parts[0], tests=tests.parts[0] if tests else None)


@dataclass(frozen=True)
class PyanNode:
    """One entity in pyan3's graph, decoded from its DOT tooltip."""

    node_id: str
    qualname: str
    module: str
    name: str
    kind: str
    path: Path | None

    def tree(self, layout: TreeLayout) -> str:
        """Which of the scanned roots this entity belongs to.

        pyan3 prefixes module names with the first path component of the target it was given, so
        the root names must be derived from the arguments rather than assumed. Hardcoding ``src``
        and ``tests`` silently empties the tests bucket on a ``lib``/``test`` layout.
        """
        head = self.module.split(".", 1)[0]
        if head == layout.source:
            return "source"
        return "tests" if head == layout.tests else "other"

    @property
    def is_public_callable(self) -> bool:
        return self.kind == "function" and not self.name.startswith("_")


@dataclass(frozen=True)
class VultureFinding:
    """One `vulture` report line."""

    path: Path
    lineno: int
    kind: str
    name: str
    confidence: int


@dataclass(frozen=True)
class RadonBlock:
    """One `radon cc` block."""

    name: str
    lineno: int
    complexity: int
    rank: str


@dataclass(frozen=True)
class ToolVersions:
    """Versions of the tools that produced a report, for provenance."""

    pyan3: str
    vulture: str
    radon: str
    grimp: str


@dataclass(frozen=True)
class ScanConfig:
    """Run parameters, carried into the report so it explains its own provenance."""

    src: Path
    tests: Path | None
    package: str
    top: int
    warn_depth: int
    modules: int
    versions: ToolVersions
    surface_skip: tuple[str, ...]
    skipped: int
    ignore_decorators: str

    @property
    def command(self) -> str:
        tests = f" --tests {self.tests}" if self.tests else ""
        return f"uv run python scripts/carpet_scan.py {self.src}{tests}"


@dataclass(frozen=True)
class SurfaceRow:
    """Check 2 — one module's public surface, bucketed by who references each name.

    The buckets come from combining two tools: pyan3 supplies cross-module edges, vulture decides
    whether a name with no external user is dead or merely used at home.
    """

    module: str
    publics: int
    tests_only: list[str] = field(default_factory=list[str])
    home_only: list[str] = field(default_factory=list[str])
    unused: list[str] = field(default_factory=list[str])
    modifiers: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])
    max_complexity: int = 0
    maintainability: float = 0.0

    @property
    def no_external(self) -> int:
        return len(self.tests_only) + len(self.home_only) + len(self.unused)

    @property
    def share(self) -> float:
        return self.no_external / self.publics if self.publics else 0.0


@dataclass(frozen=True)
class DepthRow:
    """Check 4 — one module's internal call depth, from pyan3 edges between its own callables."""

    module: str
    depth: int
    chain: list[str]
    trivial: int


@dataclass(frozen=True)
class FolderRow:
    """Check 5 — one folder's sibling-import depth, from grimp's module graph."""

    folder: str
    modules: int
    edges: int
    depth: int
    chain: list[str]
