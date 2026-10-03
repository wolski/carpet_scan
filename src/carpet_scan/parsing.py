"""Parse layer: each tool's output into typed rows. String handling over documented formats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pydot

from carpet_scan.model import PrivateAccess, PyanNode, RadonBlock, VultureFinding

# radon's and ruff's JSON shapes, declared so the parse is checked rather than cast away.
type _RadonCc = dict[str, list[dict[str, object]]]
type _RadonMi = dict[str, dict[str, object]]
type _RuffJson = list[dict[str, object]]


def _number(value: object) -> float:
    """radon emits ints and floats interchangeably; both arrive as JSON numbers."""
    return float(value) if isinstance(value, int | float) else 0.0


def _unquote(value: object) -> str:
    return str(value or "").strip('"')


def _lineno(location: object) -> int:
    """Ruff nests the line under ``location.row``; the JSON is untyped, so narrow it here."""
    if not isinstance(location, dict):
        return 0
    return int(_number(cast("dict[str, object]", location).get("row")))


def _pyan_elements(graph: pydot.Dot) -> tuple[list[pydot.Node], list[pydot.Edge]]:
    """Collect nodes and edges, descending into pyan3's `cluster_` subgraphs."""
    nodes: list[pydot.Node] = []
    edges: list[pydot.Edge] = []

    def walk(current: pydot.Graph) -> None:
        nodes.extend(current.get_node_list())
        edges.extend(current.get_edge_list())
        for sub in current.get_subgraph_list():
            walk(sub)

    walk(graph)
    return nodes, edges


def _decode_tooltip(node_id: str, tooltip: str) -> PyanNode | None:
    """Decode ``<qualname>\\n<path>:<line>\\n<kind> in <module>``.

    Node ids are not parseable on their own — pyan3 joins components with ``__``, so a module
    named ``_private`` is indistinguishable from a separator — and the tooltip is therefore the
    only reliable source of qualname, kind and module.
    """
    parts = tooltip.split("\\n")
    if len(parts) < 3 or " in " not in parts[2]:
        return None
    qualname, location, kind_line = parts[0], parts[1], parts[2]
    kind, module = kind_line.split(" in ", 1)
    name = qualname[len(module) + 1 :] if qualname.startswith(f"{module}.") else qualname
    raw_path, separator, raw_line = location.rpartition(":")
    if not separator:
        raw_path = location
    return PyanNode(
        node_id=node_id,
        qualname=qualname,
        module=module,
        name=name,
        kind=kind.strip(),
        path=Path(raw_path) if raw_path else None,
        lineno=int(raw_line) if raw_line.isdigit() else 0,
    )


def parse_pyan(dot: str) -> tuple[dict[str, PyanNode], list[tuple[str, str]]]:
    """DOT text -> (nodes by id, edges as id pairs)."""
    graphs = pydot.graph_from_dot_data(dot)
    if not graphs:
        raise RuntimeError("pyan3 produced no parseable graph")
    raw_nodes, raw_edges = _pyan_elements(graphs[0])

    nodes: dict[str, PyanNode] = {}
    for raw in raw_nodes:
        node_id = _unquote(raw.get_name())
        decoded = _decode_tooltip(node_id, _unquote(raw.get("tooltip")))
        if decoded is not None:
            nodes[node_id] = decoded

    edges = [(_unquote(e.get_source()), _unquote(e.get_destination())) for e in raw_edges]
    return nodes, [(a, b) for a, b in edges if a in nodes and b in nodes]


def parse_vulture(text: str) -> list[VultureFinding]:
    """Parse ``path:line: unused <kind> '<name>' (NN% confidence)``."""
    findings: list[VultureFinding] = []
    for line in text.splitlines():
        head, _, message = line.partition(": unused ")
        if not message or head.count(":") < 1:
            continue
        path_text, _, lineno = head.rpartition(":")
        kind, _, rest = message.partition(" '")
        name, _, tail = rest.partition("'")
        confidence = tail.strip(" ()").removesuffix("% confidence")
        findings.append(
            VultureFinding(
                path=Path(path_text),
                lineno=int(lineno) if lineno.isdigit() else 0,
                kind=kind.strip(),
                name=name,
                confidence=int(confidence) if confidence.isdigit() else 0,
            )
        )
    return findings


def parse_ruff_private(text: str) -> list[PrivateAccess]:
    """`ruff check --output-format json` -> one row per SLF001/PLC2701 finding.

    Both rules name the private member in the first backticked span of the message — SLF001 as
    ``Private member accessed: `_x` `` and PLC2701 as ``Private name import `_x` from ...``.
    """
    raw: _RuffJson = json.loads(text or "[]")
    findings: list[PrivateAccess] = []
    for entry in raw:
        message = str(entry.get("message", ""))
        quoted = message.split("`")
        findings.append(
            PrivateAccess(
                path=Path(str(entry.get("filename", ""))),
                lineno=_lineno(entry.get("location")),
                code=str(entry.get("code", "")),
                name=quoted[1] if len(quoted) > 1 else message,
            )
        )
    return findings


def parse_radon_cc(text: str) -> dict[Path, list[RadonBlock]]:
    """`radon cc -j` -> blocks per file. Paths are resolved: radon reports them relative to the
    invocation directory while pyan3 tooltips are absolute, and an unresolved key joins to nothing.
    """
    raw: _RadonCc = json.loads(text or "{}")
    blocks: dict[Path, list[RadonBlock]] = {}
    for file_text, entries in raw.items():
        blocks[Path(file_text).resolve()] = [
            RadonBlock(
                name=str(entry.get("name", "")),
                lineno=int(_number(entry.get("lineno"))),
                complexity=int(_number(entry.get("complexity"))),
                rank=str(entry.get("rank", "")),
            )
            for entry in entries
        ]
    return blocks


def parse_radon_mi(text: str) -> dict[Path, float]:
    """`radon mi -j` -> maintainability index per file, on resolved paths."""
    raw: _RadonMi = json.loads(text or "{}")
    return {
        Path(file_text).resolve(): _number(entry["mi"])
        for file_text, entry in raw.items()
        if "mi" in entry
    }
