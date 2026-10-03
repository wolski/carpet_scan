"""The join layer: bucketing and graph depth over synthetic pyan3 graphs."""

from __future__ import annotations

from pathlib import Path

import grimp

from carpet_scan.measure import (
    cohesion_result,
    depth_rows,
    leaf_imports,
    name_modifiers,
    private_rows,
    surface_rows,
)
from carpet_scan.model import PrivateAccess, PyanNode, RadonBlock, TreeLayout

LAYOUT = TreeLayout.of(Path("src"), Path("tests"))
API = Path("/w/src/pkg/api.py").resolve()


def node(module: str, name: str, kind: str = "function", path: Path = API) -> PyanNode:
    return PyanNode(
        node_id=f"{module.replace('.', '__')}__{name}",
        qualname=f"{module}.{name}",
        module=module,
        name=name,
        kind=kind,
        path=path,
    )


def graph(*nodes: PyanNode) -> dict[str, PyanNode]:
    return {n.node_id: n for n in nodes}


def test_tree_layout_is_derived_not_assumed() -> None:
    """pyan3 prefixes module names with the first component of the target it was given, so a
    `lib`/`test` project must bucket correctly without any mention of `src`."""
    layout = TreeLayout.of(Path("lib"), Path("test"))

    assert node("lib.pkg.api", "f").tree(layout) == "source"
    assert node("test.test_api", "f").tree(layout) == "tests"
    assert node("src.pkg.api", "f").tree(layout) == "other"


def test_name_modifiers_finds_the_cross_product_fingerprint() -> None:
    names = [
        "load_rule",
        "load_rule_for_version",
        "resolve_rule",
        "resolve_rule_for_version",
        "summarize_rule",
    ]

    assert name_modifiers(names)[0] == ("rule", 5)
    assert ("load", 2) not in name_modifiers(names), "below the shared-name threshold"


def test_surface_rows_bucket_by_who_references_each_name() -> None:
    nodes = graph(
        node("src.pkg.api", "used_elsewhere"),
        node("src.pkg.api", "only_tests"),
        node("src.pkg.api", "only_home"),
        node("src.pkg.api", "is_dead"),
        node("src.pkg.other", "caller", path=Path("/w/src/pkg/other.py")),
        node("tests.test_api", "test_it", path=Path("/w/tests/test_api.py")),
        node("src.pkg.api", "_private"),
    )
    edges = [
        ("src__pkg__other__caller", "src__pkg__api__used_elsewhere"),
        ("tests__test_api__test_it", "src__pkg__api__only_tests"),
    ]

    rows, omitted = surface_rows(
        nodes,
        edges,
        {(API, "is_dead")},
        {API: [RadonBlock(name="only_home", lineno=1, complexity=9, rank="C")]},
        {API: 71.25},
        frozenset(),
        LAYOUT,
    )

    assert omitted == 0
    (row,) = [r for r in rows if r.module == "src.pkg.api"]
    assert row.publics == 4, "the underscore-prefixed name is not public"
    assert (row.tests_only, row.home_only, row.unused) == (
        ["only_tests"],
        ["only_home"],
        ["is_dead"],
    )
    assert "used_elsewhere" not in row.tests_only + row.home_only + row.unused
    assert (row.max_complexity, row.maintainability) == (9, 71.25)


def test_surface_rows_report_what_they_omitted() -> None:
    """A skipped subpackage must be counted, not silently dropped: the report states the number
    so a reader can tell an empty bucket from an unscanned one."""
    nodes = graph(*(node("src.pkg.scripts.cli", f"cmd_{i}") for i in range(4)))

    rows, omitted = surface_rows(nodes, [], set(), {}, {}, frozenset({"scripts"}), LAYOUT)

    assert (rows, omitted) == ([], 1)


def test_depth_rows_measure_only_a_module_s_own_callables() -> None:
    nodes = graph(
        node("src.pkg.api", "a"),
        node("src.pkg.api", "b"),
        node("src.pkg.api", "c"),
        node("src.pkg.api", "d"),
        node("src.pkg.other", "far", path=Path("/w/src/pkg/other.py")),
    )
    edges = [
        ("src__pkg__api__a", "src__pkg__api__b"),
        ("src__pkg__api__b", "src__pkg__api__c"),
        ("src__pkg__api__c", "src__pkg__api__d"),
        # Leaving the module must not lengthen the chain: the check is intra-module.
        ("src__pkg__api__d", "src__pkg__other__far"),
    ]
    complexity = {API: [RadonBlock(name=n, lineno=1, complexity=1, rank="A") for n in "abc"]}

    rows = depth_rows(nodes, edges, complexity, LAYOUT)
    (row,) = [item for item in rows if item.module == "src.pkg.api"]

    assert (row.depth, row.chain) == (3, ["a", "b", "c", "d"])
    assert row.trivial == 3, "a, b and c are complexity 1; d has no radon block"


def test_depth_rows_survive_recursion() -> None:
    """Cycles are condensed, so mutual recursion contributes once instead of looping."""
    nodes = graph(node("src.pkg.api", "a"), node("src.pkg.api", "b"), node("src.pkg.api", "c"))
    edges = [
        ("src__pkg__api__a", "src__pkg__api__b"),
        ("src__pkg__api__b", "src__pkg__api__c"),
        ("src__pkg__api__c", "src__pkg__api__b"),
    ]

    (row,) = depth_rows(nodes, edges, {}, LAYOUT)

    assert row.depth == 1, "a -> {b, c}, the cycle condensed to one component"


def test_depth_rows_break_ties_canonically() -> None:
    """Several chains routinely tie at the longest length, and `nx.dag_longest_path` returns
    whichever it reaches first — which made the report differ between runs on identical input.
    The smallest chain must win regardless of the order the edges arrive in.
    """
    nodes = graph(
        node("src.pkg.api", "a"),
        node("src.pkg.api", "b1"),
        node("src.pkg.api", "c1"),
        node("src.pkg.api", "b2"),
        node("src.pkg.api", "c2"),
    )
    forward = [
        ("src__pkg__api__a", "src__pkg__api__b1"),
        ("src__pkg__api__b1", "src__pkg__api__c1"),
        ("src__pkg__api__a", "src__pkg__api__b2"),
        ("src__pkg__api__b2", "src__pkg__api__c2"),
    ]

    (chosen,) = depth_rows(nodes, forward, {}, LAYOUT)
    (reversed_order,) = depth_rows(nodes, list(reversed(forward)), {}, LAYOUT)

    assert chosen.chain == ["a", "b1", "c1"]
    assert reversed_order.chain == chosen.chain


def test_private_rows_group_by_the_module_doing_the_reaching() -> None:
    """That is the file that has to change, and a test reaching into a private function is the
    mechanism that keeps a carpet alive: the function looks used to a coverage gate."""
    accesses = [
        PrivateAccess(path=Path("tests/test_api.py"), lineno=1, code="SLF001", name="_a"),
        PrivateAccess(path=Path("tests/test_api.py"), lineno=2, code="SLF001", name="_a"),
        PrivateAccess(path=Path("tests/test_api.py"), lineno=3, code="PLC2701", name="_b"),
        PrivateAccess(path=Path("src/pkg/other.py"), lineno=4, code="SLF001", name="_c"),
    ]

    rows = private_rows(accesses)

    assert [r.module for r in rows] == ["tests.test_api", "src.pkg.other"], "imports rank first"
    assert (rows[0].attribute, rows[0].imported, rows[0].total) == (2, 1, 3)
    assert rows[0].names == ["_a", "_b"], "de-duplicated across the two hits on _a"


def test_cohesion_counts_an_edge_for_both_folders_it_touches() -> None:
    """Direction is not the question here. A folder everything imports and one that imports
    everything are equally uncohesive, and which end initiated is the layering gate's business."""
    modules = ["pkg.a.one", "pkg.a.two", "pkg.b.three"]
    edges = [("pkg.a.one", "pkg.a.two"), ("pkg.a.one", "pkg.b.three")]

    result = cohesion_result(modules, edges, "pkg")
    by_folder = {row.folder: row for row in result.rows}

    assert (by_folder["a"].internal, by_folder["a"].external) == (1, 1)
    assert (by_folder["b"].internal, by_folder["b"].external) == (0, 1)
    assert by_folder["a"].cohesion == 0.5
    assert result.intra == 1, "the cross-folder edge is not counted as intra for either side"


def test_cohesion_of_a_folder_with_no_possible_internal_edge_is_zero_not_undefined() -> None:
    """A one-module folder cannot have an internal edge, so the ratio is meaningless rather than
    bad. The report says `n/a`; the row still has to carry a usable number for sorting."""
    result = cohesion_result(["pkg.a.one", "pkg.b.two"], [("pkg.a.one", "pkg.b.two")], "pkg")

    assert all(row.modules == 1 for row in result.rows)
    assert all(row.cohesion == 0.0 for row in result.rows)


def test_communities_are_found_without_reference_to_folder_names() -> None:
    """The whole point: two densely connected groups filed under three folders must come back as
    two clusters, and the split must name the folder that straddles them."""
    modules = [f"pkg.x.a{i}" for i in range(4)] + [f"pkg.y.b{i}" for i in range(4)]
    edges = [(a, b) for a in modules[:4] for b in modules[:4] if a < b]
    edges += [(a, b) for a in modules[4:] for b in modules[4:] if a < b]
    edges.append(("pkg.x.a0", "pkg.y.b0"))

    result = cohesion_result(modules, edges, "pkg")

    assert len(result.communities) == 2
    assert {c.home for c in result.communities} == {"x", "y"}
    assert result.q_folders > 0, "folders that match the clusters score above chance"
    assert result.split_folders == [], "no folder straddles a cluster boundary here"


def test_a_folder_holding_two_unrelated_features_is_reported_as_split() -> None:
    """The finding worth acting on, as opposed to the layered-architecture false positive."""
    modules = ["pkg.mixed.a", "pkg.x.a1", "pkg.x.a2", "pkg.mixed.b", "pkg.y.b1", "pkg.y.b2"]
    edges = [
        ("pkg.mixed.a", "pkg.x.a1"),
        ("pkg.x.a1", "pkg.x.a2"),
        ("pkg.mixed.a", "pkg.x.a2"),
        ("pkg.mixed.b", "pkg.y.b1"),
        ("pkg.y.b1", "pkg.y.b2"),
        ("pkg.mixed.b", "pkg.y.b2"),
    ]

    result = cohesion_result(modules, edges, "pkg")

    assert result.split_folders == [("mixed", 2)]


def test_cohesion_is_reproducible_across_runs() -> None:
    """Louvain is randomised. The report carries no timestamp so it can be diffed, which only
    works if unchanged input produces an identical partition."""
    modules = [f"pkg.x.a{i}" for i in range(5)] + [f"pkg.y.b{i}" for i in range(5)]
    edges = [(a, b) for a in modules[:5] for b in modules[:5] if a < b]
    edges += [(a, b) for a in modules[5:] for b in modules[5:] if a < b]

    first = cohesion_result(modules, edges, "pkg")
    second = cohesion_result(list(reversed(modules)), list(reversed(edges)), "pkg")

    assert [c.members for c in first.communities] == [c.members for c in second.communities]
    assert first.q_communities == second.q_communities


def test_leaf_imports_drops_package_nodes() -> None:
    """Empty __init__.py files are the convention here, so a package node contributes a member
    with no edges and would dilute every ratio computed from the graph."""
    graph = grimp.build_graph("carpet_scan")

    leaves, edges = leaf_imports(graph)

    assert "carpet_scan" not in leaves
    assert "carpet_scan.measure" in leaves
    assert all(source in leaves and target in leaves for source, target in edges)
    assert ("carpet_scan.derived", "carpet_scan.measure") in edges
