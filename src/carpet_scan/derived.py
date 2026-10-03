"""Derive the current carpet report models from stored producer evidence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from carpet_scan.history import EvidenceSnapshot
from carpet_scan.measure import (
    cohesion_result_for_partition,
    depth_rows,
    folder_rows,
    private_rows,
    surface_rows,
)
from carpet_scan.model import (
    CohesionResult,
    DepthRow,
    FolderRow,
    PrivateRow,
    ScanConfig,
    SurfaceRow,
    TreeLayout,
    VultureFinding,
)


@dataclass(frozen=True)
class DerivedReport:
    """All existing report models derived from one evidence snapshot."""

    config: ScanConfig
    surface: list[SurfaceRow]
    depth: list[DepthRow]
    folders: list[FolderRow]
    dead: list[VultureFinding]
    private: list[PrivateRow]
    cohesion: CohesionResult


def derive_report(snapshot: EvidenceSnapshot) -> DerivedReport:
    """Calculate report rows without launching tools or reading live source files."""
    layout = TreeLayout.of(snapshot.config.src, snapshot.config.tests)
    dead_keys = {(finding.path.resolve(), finding.name) for finding in snapshot.vulture}
    surface, skipped = surface_rows(
        snapshot.nodes,
        snapshot.pyan_edges,
        dead_keys,
        snapshot.complexity,
        snapshot.maintainability,
        frozenset(snapshot.config.surface_skip),
        layout,
    )
    depth = depth_rows(snapshot.nodes, snapshot.pyan_edges, snapshot.complexity, layout)
    folders = folder_rows(snapshot.import_modules, snapshot.import_edges)
    partition = [set(community) for community in snapshot.communities]
    if snapshot.import_modules and not partition:
        raise RuntimeError("stored run has an import graph but no completed community partition")
    cohesion = cohesion_result_for_partition(
        snapshot.import_modules,
        snapshot.import_edges,
        snapshot.config.package,
        partition,
    )
    return DerivedReport(
        config=replace(snapshot.config, skipped=skipped),
        surface=surface,
        depth=depth,
        folders=folders,
        dead=snapshot.vulture,
        private=private_rows(snapshot.private_access),
        cohesion=cohesion,
    )


def source_locations(snapshot: EvidenceSnapshot) -> dict[str, tuple[Path, int]]:
    """Best concrete source location for each stored qualified and module name."""
    locations: dict[str, tuple[Path, int]] = {}
    for node in snapshot.nodes.values():
        if node.path is None:
            continue
        try:
            relative = node.path.relative_to(snapshot.run.project_path)
        except ValueError:
            relative = node.path
        locations.setdefault(node.module, (relative, node.lineno))
        locations[node.qualname] = (relative, node.lineno)
    return locations
