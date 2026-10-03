"""Join layer: tool rows keyed by module, bucketed. Graph maths is delegated to networkx."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import grimp
import networkx as nx

from carpet_scan.model import (
    CALLABLE_KINDS,
    COMMUNITY_SEED,
    MIN_NAMES_SHARING_A_MODIFIER,
    CohesionResult,
    CohesionRow,
    Community,
    DepthRow,
    FolderRow,
    PrivateAccess,
    PrivateRow,
    PyanNode,
    RadonBlock,
    SurfaceRow,
    TreeLayout,
)


def name_modifiers(names: list[str]) -> list[tuple[str, int]]:
    """Tokens shared by several public names — the cross-product fingerprint.

    The one measurement here with no tool behind it. It summarises names pyan3 already reported
    rather than analysing code, and it is what exposed both ``rules/loader.py`` and
    ``converters/pipeline.py`` as cross-products.
    """
    counts: dict[str, int] = {}
    for name in names:
        for token in set(name.split("_")):
            counts[token] = counts.get(token, 0) + 1
    shared = [(t, n) for t, n in counts.items() if n >= MIN_NAMES_SHARING_A_MODIFIER]
    return sorted(shared, key=lambda item: (-item[1], item[0]))


def _longest_path(graph: nx.DiGraph[str]) -> tuple[int, list[str]]:
    """Longest path, cycles condensed, ties broken canonically.

    Many modules have several paths tied at the longest length, and ``nx.dag_longest_path`` returns
    whichever it reaches first — which depends on insertion order and made the report differ between
    runs on byte-identical input. Among the longest paths this picks the lexicographically smallest,
    so the output does not depend on ordering anywhere upstream. The report is meant to be diffed
    between runs, which requires exactly this.
    """
    if graph.number_of_nodes() == 0:
        return 0, []
    condensed = nx.condensation(graph)
    label = {
        component: sorted(condensed.nodes[component]["members"]) for component in condensed.nodes
    }

    def rank(components: list[int]) -> list[list[str]]:
        return [label[component] for component in components]

    def pick(candidates: list[list[int]]) -> list[int]:
        longest = max(len(candidate) for candidate in candidates)
        return min((c for c in candidates if len(c) == longest), key=rank)

    # Canonical longest suffix after each component, in reverse topological order.
    suffix: dict[int, list[int]] = {}
    for component in reversed(list(nx.topological_sort(condensed))):
        onward = [[nxt, *suffix[nxt]] for nxt in condensed.successors(component)]
        suffix[component] = pick(onward) if onward else []

    chosen = pick([[component, *suffix[component]] for component in condensed.nodes])
    return len(chosen) - 1, [name for component in chosen for name in label[component]]


def _referencing_modules(
    nodes: dict[str, PyanNode], edges: list[tuple[str, str]]
) -> dict[str, set[str]]:
    """For each node id, the set of *other* modules that use it."""
    incoming: dict[str, set[str]] = {}
    for source, target in edges:
        if nodes[source].module != nodes[target].module:
            incoming.setdefault(target, set()).add(nodes[source].module)
    return incoming


def surface_rows(
    nodes: dict[str, PyanNode],
    edges: list[tuple[str, str]],
    dead: set[tuple[Path, str]],
    complexity: dict[Path, list[RadonBlock]],
    maintainability: dict[Path, float],
    skip: frozenset[str],
    layout: TreeLayout,
) -> tuple[list[SurfaceRow], int]:
    """Bucket every src module's public callables by who references them.

    Returns the rows and the number of modules omitted by ``skip``, so the caller can report the
    omission rather than silently truncating.
    """
    incoming = _referencing_modules(nodes, edges)
    by_module: dict[str, list[PyanNode]] = {}
    for _, node in sorted(nodes.items()):
        if node.tree(layout) == "source" and node.is_public_callable:
            by_module.setdefault(node.module, []).append(node)

    rows: list[SurfaceRow] = []
    omitted = 0
    for module, members in sorted(by_module.items()):
        if any(f".{part}." in f"{module}." for part in skip):
            omitted += 1
            continue
        tests_only: list[str] = []
        home_only: list[str] = []
        unused: list[str] = []
        for node in members:
            users = incoming.get(node.node_id, set())
            if any(user.split(".", 1)[0] == layout.source for user in users):
                continue
            if users:
                tests_only.append(node.name)
            elif (_resolve(node.path), node.name) in dead:
                unused.append(node.name)
            else:
                home_only.append(node.name)
        path = _resolve(members[0].path)
        blocks = complexity.get(path, [])
        rows.append(
            SurfaceRow(
                module=module,
                publics=len(members),
                tests_only=sorted(tests_only),
                home_only=sorted(home_only),
                unused=sorted(unused),
                modifiers=name_modifiers([n.name for n in members]),
                max_complexity=max((b.complexity for b in blocks), default=0),
                maintainability=maintainability.get(path, 0.0),
            )
        )
    ranked = sorted(rows, key=lambda r: (-len(r.unused), -len(r.home_only), -r.share, -r.publics))
    return ranked, omitted


def depth_rows(
    nodes: dict[str, PyanNode],
    edges: list[tuple[str, str]],
    complexity: dict[Path, list[RadonBlock]],
    layout: TreeLayout,
) -> list[DepthRow]:
    """Call depth inside each src module, over pyan3 edges between its own callables."""
    callables = {
        node_id: node
        for node_id, node in nodes.items()
        if node.tree(layout) == "source" and node.kind in CALLABLE_KINDS
    }
    by_module: dict[str, list[str]] = {}
    for node_id, node in sorted(callables.items()):
        by_module.setdefault(node.module, []).append(node_id)

    rows: list[DepthRow] = []
    for module, members in by_module.items():
        # Sorted insertion order is what makes the chosen chain reproducible: several paths
        # often tie at the longest length, and networkx returns whichever it reaches first.
        member_set = set(members)
        graph: nx.DiGraph[str] = nx.DiGraph()
        graph.add_nodes_from(sorted(members))
        graph.add_edges_from(
            sorted((a, b) for a, b in edges if a in member_set and b in member_set)
        )
        depth, chain = _longest_path(graph)
        path = _resolve(callables[members[0]].path)
        simple = {b.name for b in complexity.get(path, []) if b.complexity == 1}
        names = [callables[n].name for n in chain]
        rows.append(
            DepthRow(
                module=module,
                depth=depth,
                chain=names,
                trivial=sum(1 for name in names if name in simple),
            )
        )
    return sorted(rows, key=lambda r: (-r.depth, r.module))


def private_rows(accesses: list[PrivateAccess]) -> list[PrivateRow]:
    """Cross-boundary private access per accessing module, from Ruff's findings.

    Grouped by the module doing the reaching rather than the one being reached into, because that
    is the file that has to change — and because a test reaching into a private function is the
    mechanism that keeps a carpet alive: the function looks used to a coverage gate.
    """
    grouped: dict[str, list[PrivateAccess]] = {}
    for access in accesses:
        grouped.setdefault(_dotted(access.path), []).append(access)

    rows = [
        PrivateRow(
            module=module,
            attribute=sum(1 for a in found if a.code == "SLF001"),
            imported=sum(1 for a in found if a.code == "PLC2701"),
            names=sorted({a.name for a in found}),
        )
        for module, found in sorted(grouped.items())
    ]
    return sorted(rows, key=lambda r: (-r.imported, -r.total, r.module))


def import_graph(package: str) -> grimp.ImportGraph:
    """The module import graph. One build serves both Check 5 and the cohesion report."""
    return grimp.build_graph(package)


def leaf_imports(graph: grimp.ImportGraph) -> tuple[list[str], list[tuple[str, str]]]:
    """Modules that are files, and the import edges between them.

    Package nodes are dropped: ``__init__.py`` is kept empty by convention, so a package node
    contributes a member with no edges and would dilute every ratio computed from the graph.
    """
    everything = set(graph.modules)
    packages = {
        parent
        for module in everything
        for parent in [module.rsplit(".", 1)[0]]
        if parent in everything
    }
    leaves = sorted(everything - packages)
    known = set(leaves)
    edges = [
        (module, imported)
        for module in leaves
        for imported in sorted(graph.find_modules_directly_imported_by(module))
        if imported in known and imported != module
    ]
    return leaves, edges


def folder_of(module: str, package: str) -> str:
    """The folder a module lives in, nested ones included, e.g. ``params.parsers``."""
    parts = module.removeprefix(f"{package}.").split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else "<root>"


def _modularity(graph: nx.Graph[str], partition: list[set[str]]) -> float:
    """How much better than chance a partition explains the edges, on networkx's scale.

    Wrapped only to confine the cast: networkx ships no return type for ``modularity``, and
    letting an unknown propagate into ``CohesionResult`` would silently untype both fields.
    """
    if graph.number_of_edges() == 0:
        return 0.0
    return cast(float, nx.community.modularity(graph, partition))


def cohesion_result(
    modules: list[str],
    edges: list[tuple[str, str]],
    package: str,
) -> CohesionResult:
    """Compare the folder partition against the clusters the import graph actually has.

    Takes plain module names and edges rather than a grimp object so the measurement is
    testable without an importable package on disk; ``leaf_imports`` is the adapter that
    supplies them.

    The community search is seeded so two runs over unchanged code produce identical reports —
    the Markdown is meant to be diffed, and an unseeded Louvain pass would show spurious
    movement in the cluster tables on every run.
    """
    graph: nx.Graph[str] = nx.Graph()
    graph.add_nodes_from(modules)
    graph.add_edges_from(edges)
    partition = [
        set(group) for group in nx.community.louvain_communities(graph, seed=COMMUNITY_SEED)
    ]
    return cohesion_result_for_partition(modules, edges, package, partition)


def cohesion_result_for_partition(
    modules: list[str],
    edges: list[tuple[str, str]],
    package: str,
    partition: list[set[str]],
) -> CohesionResult:
    """Calculate folder cohesion from an already-recorded graph partition."""
    folder = {module: folder_of(module, package) for module in modules}
    internal: dict[str, int] = {}
    external: dict[str, int] = {}
    size: dict[str, int] = {}
    for module in modules:
        size[folder[module]] = size.get(folder[module], 0) + 1
    for source, target in edges:
        if folder[source] == folder[target]:
            internal[folder[source]] = internal.get(folder[source], 0) + 1
        else:
            for side in (source, target):
                external[folder[side]] = external.get(folder[side], 0) + 1

    rows = [
        CohesionRow(
            folder=name,
            modules=count,
            internal=internal.get(name, 0),
            external=external.get(name, 0),
        )
        for name, count in size.items()
    ]
    rows.sort(key=lambda row: (-row.cohesion, -row.modules, row.folder))

    undirected: nx.Graph[str] = nx.Graph()
    undirected.add_nodes_from(modules)
    for source, target in edges:
        weight = undirected.edges.get((source, target), {}).get("weight", 0)
        undirected.add_edge(source, target, weight=weight + 1)

    by_folder: dict[str, set[str]] = {}
    for module in modules:
        by_folder.setdefault(folder[module], set()).add(module)
    communities: list[Community] = []
    for members in partition:
        spread: dict[str, int] = {}
        for module in members:
            spread[folder[module]] = spread.get(folder[module], 0) + 1
        communities.append(
            Community(
                members=sorted(module.removeprefix(f"{package}.") for module in members),
                spread=sorted(spread.items(), key=lambda item: (-item[1], item[0])),
            )
        )
    communities.sort(key=lambda community: (-len(community.members), community.home))

    return CohesionResult(
        rows=rows,
        communities=communities,
        edges=len(edges),
        intra=sum(internal.values()),
        q_folders=_modularity(undirected, list(by_folder.values())),
        q_communities=_modularity(undirected, partition),
    )


def folder_rows(modules: list[str], edges: list[tuple[str, str]]) -> list[FolderRow]:
    """Sibling-import depth per folder, from grimp's module graph."""
    members: dict[str, list[str]] = {}
    for module in sorted(modules):
        parent = module.rsplit(".", 1)[0] if "." in module else ""
        if parent:
            members.setdefault(parent, []).append(module)

    rows: list[FolderRow] = []
    for folder, group in members.items():
        siblings = set(group)
        digraph: nx.DiGraph[str] = nx.DiGraph()
        digraph.add_nodes_from(sorted(group))
        digraph.add_edges_from(
            sorted(
                (module, imported)
                for module, imported in edges
                if module in siblings and imported in siblings and imported != module
            )
        )
        depth, chain = _longest_path(digraph)
        rows.append(
            FolderRow(
                folder=folder,
                modules=len(group),
                edges=digraph.number_of_edges(),
                depth=depth,
                chain=[c.rsplit(".", 1)[-1] for c in chain],
            )
        )
    return sorted(rows, key=lambda r: (-r.depth, -r.edges, r.folder))


def _resolve(path: Path | None) -> Path:
    return path.resolve() if path else Path()


def _dotted(path: Path) -> str:
    """A file path as a dotted module label, matching the keys the other tables use.

    Ruff reports absolute paths; relative-to-cwd keeps the label comparable with pyan3's module
    names so one prefix strip serves every table.
    """
    try:
        relative = path.resolve().relative_to(Path.cwd())
    except ValueError:
        relative = path
    return ".".join(relative.with_suffix("").parts)
