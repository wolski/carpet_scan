"""Typed rows. Every field traces to a tool's output; nothing here measures anything."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

MIN_PUBLICS_FOR_SURFACE_REPORT = 4
MIN_NAMES_SHARING_A_MODIFIER = 3
FOLDER_DEPTH_WARN = 3
VULTURE_MIN_CONFIDENCE = 60
# Louvain is randomised. A fixed seed keeps two runs over unchanged code byte-identical, which
# the Markdown report depends on — it carries no timestamp so that it can be diffed between runs.
COMMUNITY_SEED = 0
# Below this, a folder's cohesion is noise rather than a measurement: one edge either way moves
# the ratio across the whole range.
MIN_MODULES_FOR_COHESION = 2
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
    lineno: int = 0

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
class PrivateAccess:
    """One Ruff finding: a private name touched from outside the module that defines it."""

    path: Path
    lineno: int
    code: str
    name: str


@dataclass(frozen=True)
class ToolVersions:
    """Versions of the tools that produced a report, for provenance."""

    pyan3: str
    vulture: str
    radon: str
    grimp: str
    ruff: str
    networkx: str = "unknown"
    carpet_scan: str = "unknown"


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
        """The invocation that reproduces this report.

        `uv run`, not `uvx`: grimp resolves the target package through the Python path, so the
        scan must run inside the target project's own environment.
        """
        tests = f" --tests {self.tests}" if self.tests else ""
        return f"uv run carpet-scan {self.src}{tests}"


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
class PrivateRow:
    """Check 1 — one module's reach into private names defined elsewhere.

    Keyed by the module doing the reaching, which is the one that has to change.
    """

    module: str
    attribute: int
    imported: int
    names: list[str] = field(default_factory=list[str])

    @property
    def total(self) -> int:
        return self.attribute + self.imported


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


@dataclass(frozen=True)
class CohesionRow:
    """One folder's share of import edges that stay inside it.

    ``external`` counts every edge with exactly one end in the folder, in either direction, so
    a folder that is imported everywhere and one that imports everything read the same. The
    distinction that matters here is inside versus outside, not who initiated.
    """

    folder: str
    modules: int
    internal: int
    external: int

    @property
    def total(self) -> int:
        return self.internal + self.external

    @property
    def cohesion(self) -> float:
        return self.internal / self.total if self.total else 0.0


@dataclass(frozen=True)
class Community:
    """One cluster the import graph actually has, found without reference to folder names."""

    members: list[str]
    spread: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])

    @property
    def folders(self) -> int:
        """How many folders this one cluster is spread across. 1 means folder and cluster agree."""
        return len(self.spread)

    @property
    def home(self) -> str:
        """The folder holding most of this cluster — the name it would have if it were one."""
        return self.spread[0][0] if self.spread else ""


@dataclass(frozen=True)
class CohesionResult:
    """Whether the declared folders are the clusters the import graph has.

    ``q_folders`` scores the partition the folder tree declares; ``q_communities`` scores the
    best partition the graph itself admits. The gap between them is the measurement — a large
    one means the packaging and the coupling disagree, which is a finding to triage and not a
    defect on its own: a deliberately layered design partitions across features and will always
    score below a clustering found by maximising modularity.
    """

    rows: list[CohesionRow]
    communities: list[Community]
    edges: int
    intra: int
    q_folders: float
    q_communities: float

    @property
    def intra_share(self) -> float:
        return self.intra / self.edges if self.edges else 0.0

    @property
    def split_folders(self) -> list[tuple[str, int]]:
        """Folders whose modules land in more than one cluster, worst first."""
        counts: dict[str, int] = {}
        for community in self.communities:
            for folder, _ in community.spread:
                counts[folder] = counts.get(folder, 0) + 1
        return sorted(
            ((folder, pieces) for folder, pieces in counts.items() if pieces > 1),
            key=lambda item: (-item[1], item[0]),
        )
