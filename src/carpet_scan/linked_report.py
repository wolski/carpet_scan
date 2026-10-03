"""Source-linked report representation built exclusively from stored evidence."""

from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import quote

from carpet_scan.derived import DerivedReport, derive_report
from carpet_scan.history import EvidenceSnapshot, load_snapshot
from carpet_scan.measure import folder_of
from carpet_scan.report import (
    render_cohesion_markdown,
    render_markdown,
    short_name,
)


def _relative_source(snapshot: EvidenceSnapshot, path: Path) -> Path:
    try:
        return path.relative_to(snapshot.run.project_path)
    except ValueError:
        return path


def _source_url(snapshot: EvidenceSnapshot, path: Path, line: int) -> str | None:
    repository = snapshot.run.repository_url
    commit = snapshot.run.commit_sha
    if repository is None or commit is None:
        return None
    relative = _relative_source(snapshot, path).as_posix()
    base = repository.removesuffix(".git")
    anchor = f"#L{line}" if line else ""
    return f"{base}/blob/{commit}/{quote(relative)}{anchor}"


def _code_link(label: str, url: str | None) -> str:
    return f"[`{label}`]({url})" if url is not None else f"`{label}`"


def _module_locations(snapshot: EvidenceSnapshot) -> dict[str, tuple[Path, int]]:
    locations: dict[str, tuple[Path, int]] = {}
    for node in snapshot.nodes.values():
        if node.path is not None:
            locations.setdefault(node.module, (node.path, node.lineno))
    return locations


def _link_module_cells(markdown: str, snapshot: EvidenceSnapshot) -> str:
    linked = markdown
    for module, (path, line) in _module_locations(snapshot).items():
        label = short_name(module, snapshot.config.package)
        url = _source_url(snapshot, path, line)
        if url is not None:
            linked = linked.replace(f"| `{label}` |", f"| {_code_link(label, url)} |")
    return linked


def _source_index(snapshot: EvidenceSnapshot) -> str:
    rows = [
        "## Source index",
        "",
        "Concrete functions and methods retained from the pyan evidence. GitHub runs link to the",
        "immutable scanned revision; local runs show copyable repository-relative locations.",
        "",
        "| symbol | kind | source |",
        "| --- | --- | --- |",
    ]
    for node in sorted(
        snapshot.nodes.values(), key=lambda item: (item.module, item.lineno, item.name)
    ):
        if node.path is None:
            continue
        relative = _relative_source(snapshot, node.path)
        location = f"{relative.as_posix()}:{node.lineno}" if node.lineno else relative.as_posix()
        rows.append(
            f"| `{node.qualname}` | {node.kind} | "
            f"{_code_link(location, _source_url(snapshot, node.path, node.lineno))} |"
        )
    return "\n".join(rows) + "\n"


def _dependency_matrix(snapshot: EvidenceSnapshot) -> str:
    folders = sorted(
        {folder_of(module, snapshot.config.package) for module in snapshot.import_modules}
    )
    weights: dict[tuple[str, str], int] = {}
    for source, target in snapshot.import_edges:
        pair = (
            folder_of(source, snapshot.config.package),
            folder_of(target, snapshot.config.package),
        )
        weights[pair] = weights.get(pair, 0) + 1
    head = "".join(f"<th>{html.escape(folder)}</th>" for folder in folders)
    rows: list[str] = []
    for source in folders:
        cells = "".join(
            f'<td class="{("diagonal" if source == target else "")}">'
            f"{weights.get((source, target), 0) or '·'}</td>"
            for target in folders
        )
        rows.append(f"<tr><th>{html.escape(source)}</th>{cells}</tr>")
    return (
        "<section><h2>Stored dependency graph</h2><p>Folder-level import matrix from the retained "
        "Grimp module edges. Row imports column; the diagonal contains within-folder edges.</p>"
        f'<div class="graph-scroll"><table class="dependency-matrix"><thead><tr><th></th>{head}'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def _chain_location(snapshot: EvidenceSnapshot, module: str, name: str) -> tuple[Path, int] | None:
    matches = [
        node
        for node in snapshot.nodes.values()
        if node.module == module and node.name == name and node.path is not None
    ]
    if not matches:
        return None
    node = min(matches, key=lambda item: (item.lineno, item.qualname))
    if node.path is None:
        return None
    return node.path, node.lineno


def _call_chain_svg(snapshot: EvidenceSnapshot, module: str, chain: list[str]) -> str:
    node_width = 150
    gap = 54
    width = max(360, len(chain) * node_width + max(0, len(chain) - 1) * gap + 40)
    parts = [f'<svg class="call-chain" viewBox="0 0 {width} 90" role="img">']
    for index in range(len(chain) - 1):
        start = 20 + index * (node_width + gap) + node_width
        end = start + gap
        parts.append(f'<path class="call-edge" d="M{start},45 L{end},45"/>')
        parts.append(f'<path class="call-arrow" d="M{end - 7},40 L{end},45 L{end - 7},50"/>')
    for index, name in enumerate(chain):
        x = 20 + index * (node_width + gap)
        location = _chain_location(snapshot, module, name)
        url = _source_url(snapshot, *location) if location is not None else None
        opening = f'<a href="{html.escape(url)}" target="_blank">' if url else "<g>"
        closing = "</a>" if url else "</g>"
        parts.extend(
            [
                opening,
                f'<rect class="call-node" x="{x}" y="24" width="{node_width}" height="42" rx="4"/>',
                f'<text x="{x + 10}" y="50">{html.escape(name)}</text>',
                closing,
            ]
        )
    parts.append("</svg>")
    return "".join(parts)


def _call_graphs(snapshot: EvidenceSnapshot, derived: DerivedReport) -> str:
    rows = [row for row in derived.depth if row.depth >= derived.config.warn_depth]
    if not rows:
        return (
            "<section><h2>Stored call graph</h2><p>No module reaches the configured display "
            "depth. All pyan nodes and edges remain stored.</p></section>"
        )
    diagrams = "".join(
        f"<h3><code>{html.escape(short_name(row.module, derived.config.package))}</code></h3>"
        f'<div class="graph-scroll">{_call_chain_svg(snapshot, row.module, row.chain)}</div>'
        for row in rows
    )
    return (
        "<section><h2>Stored call graph</h2><p>The deepest retained intra-module call chain for "
        f"each displayed module. Arrows follow pyan uses-edges.</p>{diagrams}</section>"
    )


def _graph_sections(snapshot: EvidenceSnapshot, derived: DerivedReport) -> str:
    return _dependency_matrix(snapshot) + _call_graphs(snapshot, derived)


def render_linked_report(database: Path, run_id: str) -> str:
    """Render both current reports plus source links from one stored run."""
    snapshot = load_snapshot(database, run_id)
    derived = derive_report(snapshot)
    carpet = render_markdown(
        derived.surface,
        derived.depth,
        derived.folders,
        derived.dead,
        derived.private,
        derived.config,
    )
    cohesion = render_cohesion_markdown(derived.cohesion, derived.config)
    provenance = [
        "# Stored carpet evidence",
        "",
        f"Run: `{run_id}`  ",
        f"Target: `{snapshot.run.target_kind}`  ",
        f"Source commit: `{snapshot.run.commit_sha or snapshot.run.head_sha or 'not recorded'}`  ",
        f"Dirty local worktree: `{str(snapshot.run.dirty).lower()}`",
        "",
        "These are gauges for human triage, never pass/fail judgements.",
        "",
    ]
    combined = "\n".join(provenance) + carpet + "\n---\n\n" + cohesion
    return (
        _link_module_cells(combined, snapshot)
        + "\n"
        + _graph_sections(snapshot, derived)
        + "\n"
        + _source_index(snapshot)
    )
