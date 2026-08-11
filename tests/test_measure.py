"""The join layer: bucketing and graph depth over synthetic pyan3 graphs."""

from __future__ import annotations

from pathlib import Path

from carpet_scan.measure import depth_rows, name_modifiers, private_rows, surface_rows
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

    (row,) = depth_rows(nodes, edges, complexity, 3, LAYOUT)

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

    (row,) = depth_rows(nodes, edges, {}, 1, LAYOUT)

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

    (chosen,) = depth_rows(nodes, forward, {}, 2, LAYOUT)
    (reversed_order,) = depth_rows(nodes, list(reversed(forward)), {}, 2, LAYOUT)

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
