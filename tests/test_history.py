"""Evidence archive, schema ingestion, re-ingestion, and stored derivation."""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from carpet_scan.derived import derive_report
from carpet_scan.evidence import Artifact, ProducerResult, TargetRecord, seal_evidence
from carpet_scan.history import connect, delete_run, ingest_archive, load_snapshot
from carpet_scan.linked_report import render_linked_report
from carpet_scan.model import ScanConfig, ToolVersions

PYAN = b"""digraph G {
subgraph cluster_src {
  src__pkg__api__load [tooltip="src.pkg.api.load\\n/w/project/src/pkg/api.py:5\\nfunction in src.pkg.api"];
  src__pkg__api__helper [tooltip="src.pkg.api._helper\\n/w/project/src/pkg/api.py:9\\nfunction in src.pkg.api"];
}
src__pkg__api__load -> src__pkg__api__helper;
}
"""


def producer(name: str, artifact_name: str, media_type: str, content: bytes) -> ProducerResult:
    return ProducerResult(
        producer=name,
        version="1.2.3",
        operation=[name, "--json"],
        status="complete",
        started_at_utc="2026-08-13T10:00:00.000000Z",
        ended_at_utc="2026-08-13T10:00:01.000000Z",
        duration_seconds=1.0,
        exit_code=0,
        artifacts=[Artifact(artifact_name, name, media_type, 1, content)],
    )


def fixture_producers() -> list[ProducerResult]:
    ruff = [
        {
            "code": "SLF001",
            "message": "Private member accessed: `_helper`",
            "filename": "/w/project/tests/test_api.py",
            "location": {"row": 12, "column": 5},
            "end_location": {"row": 12, "column": 12},
            "fix": {
                "applicability": "safe",
                "message": "replace",
                "edits": [
                    {
                        "content": "helper",
                        "location": {"row": 12, "column": 5},
                        "end_location": {"row": 12, "column": 12},
                    }
                ],
            },
            "noqa_row": 12,
            "url": "https://docs.astral.sh/ruff/rules/private-member-access/",
        }
    ]
    radon_cc = {
        "src/pkg/api.py": [
            {
                "type": "class",
                "name": "Api",
                "lineno": 1,
                "col_offset": 0,
                "endline": 20,
                "end_col_offset": 0,
                "complexity": 3,
                "rank": "A",
                "methods": [
                    {
                        "type": "method",
                        "name": "load",
                        "lineno": 5,
                        "col_offset": 4,
                        "endline": 8,
                        "end_col_offset": 10,
                        "complexity": 2,
                        "rank": "A",
                    }
                ],
            }
        ]
    }
    graph = {
        "modules": [
            {"name": "pkg.api", "is_package": False, "is_leaf": True},
            {"name": "pkg.other", "is_package": False, "is_leaf": True},
        ],
        "edges": [["pkg.other", "pkg.api"]],
    }
    communities = {
        "algorithm": "louvain_communities",
        "seed": 0,
        "package": "pkg",
        "communities": [["pkg.api", "pkg.other"]],
    }
    return [
        producer("pyan", "tools/pyan/graph.dot", "text/vnd.graphviz", PYAN),
        producer(
            "vulture",
            "tools/vulture/findings.txt",
            "text/plain",
            b"src/pkg/api.py:9: unused function '_helper' (60% confidence)\n",
        ),
        producer(
            "radon_cc",
            "tools/radon/cc.json",
            "application/json",
            json.dumps(radon_cc).encode(),
        ),
        producer(
            "radon_mi",
            "tools/radon/mi.json",
            "application/json",
            b'{"src/pkg/api.py":{"mi":72.5,"rank":"A"}}',
        ),
        producer(
            "ruff",
            "tools/ruff/findings.json",
            "application/json",
            json.dumps(ruff).encode(),
        ),
        producer(
            "grimp",
            "tools/grimp/import_graph.json",
            "application/json",
            json.dumps(graph).encode(),
        ),
        producer(
            "networkx",
            "tools/networkx/communities.json",
            "application/json",
            json.dumps(communities).encode(),
        ),
    ]


def config(project: Path) -> ScanConfig:
    return ScanConfig(
        src=project / "src",
        tests=project / "tests",
        package="pkg",
        top=12,
        warn_depth=3,
        modules=0,
        versions=ToolVersions("1", "1", "1", "1", "1", "1", "1"),
        surface_skip=(),
        skipped=0,
        ignore_decorators="@app.command",
    )


def test_archive_round_trips_complete_normalized_evidence(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "src").mkdir()
    (project / "tests").mkdir()
    target = TargetRecord(
        kind="folder",
        project_identity=f"folder:{project}",
        project_path=str(project),
        repository_url=None,
        commit_sha=None,
        head_sha=None,
        dirty=True,
    )
    sealed = seal_evidence(
        project / ".carpet",
        datetime(2026, 8, 13, 10, tzinfo=UTC),
        target,
        config(project),
        fixture_producers(),
    )
    database = project / ".carpet" / "history.sqlite3"

    run_id = ingest_archive(database, sealed.archive)

    with zipfile.ZipFile(sealed.archive) as bundle:
        assert bundle.testzip() is None
        assert "manifest.json" in bundle.namelist()
    with connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM pyan_node").fetchone()[0] == 2
        assert (
            connection.execute("SELECT line FROM pyan_node ORDER BY line LIMIT 1").fetchone()[0]
            == 5
        )
        assert connection.execute("SELECT count(*) FROM radon_cc_block").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM ruff_fix_edit").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM import_edge").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM community_member").fetchone()[0] == 2

    snapshot = load_snapshot(database, run_id)
    report = derive_report(snapshot)
    assert report.depth[0].chain == ["load", "_helper"]
    assert report.cohesion.edges == 1
    linked = render_linked_report(database, run_id)
    assert "Stored dependency graph" in linked
    assert "Stored call graph" in linked
    assert "Source index" in linked

    delete_run(database, run_id)
    rebuilt = ingest_archive(database, sealed.archive)
    assert rebuilt == run_id
    with connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM pyan_edge").fetchone()[0] == 1
