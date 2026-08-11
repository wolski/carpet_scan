"""Each tool's output format, parsed. Fixtures are real captured output, trimmed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from carpet_scan.parsing import (
    parse_pyan,
    parse_radon_cc,
    parse_radon_mi,
    parse_ruff_private,
    parse_vulture,
)

# pyan3 nests nodes in `cluster_` subgraphs and encodes the only reliable metadata in the
# tooltip: node ids join components with `__`, so `a__b` is ambiguous between `a.b` and `a_b`.
PYAN_DOT = """
digraph G {
    graph [rankdir=LR];
    subgraph cluster_src {
        label="src";
        subgraph cluster_src__pkg {
            label="pkg";
            src__pkg__reader__read_tsv [tooltip="src.pkg.reader.read_tsv\\n/w/src/pkg/reader.py:12\\nfunction in src.pkg.reader"];
            src__pkg__reader___open [tooltip="src.pkg.reader._open\\n/w/src/pkg/reader.py:30\\nfunction in src.pkg.reader"];
            src__pkg__api__load [tooltip="src.pkg.api.load\\n/w/src/pkg/api.py:5\\nfunction in src.pkg.api"];
        }
    }
    src__pkg__api__load -> src__pkg__reader__read_tsv;
    src__pkg__reader__read_tsv -> src__pkg__reader___open;
    src__pkg__api__load -> not_a_declared_node;
}
"""


def test_parse_pyan_decodes_tooltips_across_subgraphs() -> None:
    nodes, edges = parse_pyan(PYAN_DOT)

    assert set(nodes) == {
        "src__pkg__reader__read_tsv",
        "src__pkg__reader___open",
        "src__pkg__api__load",
    }
    reader = nodes["src__pkg__reader__read_tsv"]
    assert (reader.module, reader.name, reader.kind) == ("src.pkg.reader", "read_tsv", "function")
    assert reader.path == Path("/w/src/pkg/reader.py")
    assert ("src__pkg__api__load", "src__pkg__reader__read_tsv") in edges


def test_parse_pyan_drops_edges_to_undecodable_nodes() -> None:
    """An edge whose endpoint carried no usable tooltip must not survive: downstream code
    indexes `nodes[source]` unguarded, so a dangling id would raise mid-measurement."""
    _, edges = parse_pyan(PYAN_DOT)

    assert all(target != "not_a_declared_node" for _, target in edges)


def test_parse_pyan_rejects_empty_output() -> None:
    with pytest.raises(RuntimeError, match="no parseable graph"):
        parse_pyan("")


def test_parse_vulture_reads_kind_name_and_confidence() -> None:
    text = (
        "src/pkg/api.py:41: unused function 'has_stored_rule' (60% confidence)\n"
        "src/pkg/reader.py:9: unused variable 'header' (100% confidence)\n"
        "\n"
        "some unrelated line\n"
    )

    findings = parse_vulture(text)

    assert [(f.kind, f.name, f.lineno, f.confidence) for f in findings] == [
        ("function", "has_stored_rule", 41, 60),
        ("variable", "header", 9, 100),
    ]
    assert findings[0].path == Path("src/pkg/api.py")


def test_parse_radon_resolves_paths() -> None:
    """radon reports paths relative to the invocation directory while pyan3 tooltips are
    absolute. Both sides must resolve or the per-module join silently yields zeros."""
    cc = json.dumps(
        {"src/pkg/api.py": [{"name": "load", "lineno": 5, "complexity": 7, "rank": "B"}]}
    )
    mi = json.dumps({"src/pkg/api.py": {"mi": 62.5, "rank": "A"}})

    blocks = parse_radon_cc(cc)
    scores = parse_radon_mi(mi)

    expected = Path("src/pkg/api.py").resolve()
    assert expected.is_absolute()
    assert blocks[expected][0].complexity == 7
    assert scores[expected] == pytest.approx(62.5)


def test_parse_radon_tolerates_empty_and_partial_output() -> None:
    """radon emits ints and floats interchangeably, and skips files it cannot parse."""
    assert parse_radon_cc("") == {}
    assert parse_radon_mi("") == {}
    assert parse_radon_mi(json.dumps({"src/broken.py": {"error": "cannot parse"}})) == {}
    blocks = parse_radon_cc(json.dumps({"a.py": [{"name": "f", "lineno": 1, "complexity": 3}]}))
    assert blocks[Path("a.py").resolve()][0].rank == ""


def test_parse_ruff_private_extracts_the_private_name() -> None:
    """Both rules name the member in the first backticked span, but in different sentences."""
    text = json.dumps(
        [
            {
                "code": "SLF001",
                "filename": "/w/tests/test_api.py",
                "location": {"row": 12, "column": 5},
                "message": "Private member accessed: `_materialize_column_group`",
            },
            {
                "code": "PLC2701",
                "filename": "/w/tests/test_cli.py",
                "location": {"row": 23, "column": 5},
                "message": (
                    "Private name import `_write_atomically` from external module `pkg.scripts.cli`"
                ),
            },
        ]
    )

    findings = parse_ruff_private(text)

    assert [(f.code, f.name, f.lineno) for f in findings] == [
        ("SLF001", "_materialize_column_group", 12),
        ("PLC2701", "_write_atomically", 23),
    ]


def test_parse_ruff_private_tolerates_a_clean_run() -> None:
    """Ruff prints `[]` when it finds nothing, and exits 0 — both must be non-events."""
    assert parse_ruff_private("[]") == []
    assert parse_ruff_private("") == []
