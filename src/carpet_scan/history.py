"""Versioned SQLite ingestion and queries for archived producer evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from carpet_scan.model import (
    PrivateAccess,
    PyanNode,
    RadonBlock,
    ScanConfig,
    ToolVersions,
    VultureFinding,
)
from carpet_scan.parsing import parse_pyan, parse_vulture

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
    project_id INTEGER PRIMARY KEY,
    identity TEXT NOT NULL UNIQUE,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('github', 'folder'))
);
CREATE TABLE IF NOT EXISTS run (
    run_id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES project(project_id),
    target_kind TEXT NOT NULL CHECK (target_kind IN ('github', 'folder')),
    started_at_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('complete', 'partial', 'failed')),
    project_path TEXT NOT NULL,
    repository_url TEXT,
    commit_sha TEXT,
    head_sha TEXT,
    dirty INTEGER NOT NULL CHECK (dirty IN (0, 1)),
    configuration_json TEXT NOT NULL,
    python_version TEXT NOT NULL,
    platform TEXT NOT NULL,
    carpet_scan_version TEXT NOT NULL,
    archive_path TEXT NOT NULL,
    archive_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS producer_run (
    run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    producer TEXT NOT NULL,
    version TEXT NOT NULL,
    operation_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('complete', 'failed', 'unavailable')),
    started_at_utc TEXT NOT NULL,
    ended_at_utc TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    exit_code INTEGER,
    error TEXT,
    PRIMARY KEY (run_id, producer)
);
CREATE TABLE IF NOT EXISTS artifact (
    run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    producer TEXT NOT NULL,
    media_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (run_id, name),
    FOREIGN KEY (run_id, producer) REFERENCES producer_run(run_id, producer)
);
CREATE TABLE IF NOT EXISTS pyan_node (
    run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    qualname TEXT NOT NULL,
    module TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT,
    line INTEGER NOT NULL,
    PRIMARY KEY (run_id, node_id)
);
CREATE TABLE IF NOT EXISTS pyan_edge (
    run_id TEXT NOT NULL,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (run_id, source_node_id, target_node_id),
    FOREIGN KEY (run_id, source_node_id) REFERENCES pyan_node(run_id, node_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, target_node_id) REFERENCES pyan_node(run_id, node_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS vulture_finding (
    run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    path TEXT NOT NULL,
    line INTEGER NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    confidence INTEGER NOT NULL,
    raw_text TEXT NOT NULL,
    PRIMARY KEY (run_id, position)
);
CREATE TABLE IF NOT EXISTS radon_cc_block (
    block_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    parent_block_id INTEGER REFERENCES radon_cc_block(block_id),
    position INTEGER NOT NULL,
    path TEXT NOT NULL,
    block_type TEXT NOT NULL,
    name TEXT NOT NULL,
    line INTEGER NOT NULL,
    column_offset INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    end_column_offset INTEGER NOT NULL,
    complexity INTEGER NOT NULL,
    rank TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS radon_mi_file (
    run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    mi REAL,
    rank TEXT,
    error TEXT,
    PRIMARY KEY (run_id, path)
);
CREATE TABLE IF NOT EXISTS ruff_finding (
    finding_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    filename TEXT NOT NULL,
    start_row INTEGER NOT NULL,
    start_column INTEGER NOT NULL,
    end_row INTEGER NOT NULL,
    end_column INTEGER NOT NULL,
    noqa_row INTEGER,
    url TEXT,
    UNIQUE (run_id, position)
);
CREATE TABLE IF NOT EXISTS ruff_fix (
    finding_id INTEGER PRIMARY KEY REFERENCES ruff_finding(finding_id) ON DELETE CASCADE,
    applicability TEXT,
    message TEXT
);
CREATE TABLE IF NOT EXISTS ruff_fix_edit (
    finding_id INTEGER NOT NULL REFERENCES ruff_fix(finding_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    content TEXT NOT NULL,
    start_row INTEGER NOT NULL,
    start_column INTEGER NOT NULL,
    end_row INTEGER NOT NULL,
    end_column INTEGER NOT NULL,
    PRIMARY KEY (finding_id, position)
);
CREATE TABLE IF NOT EXISTS import_module (
    run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    module TEXT NOT NULL,
    is_package INTEGER NOT NULL CHECK (is_package IN (0, 1)),
    is_leaf INTEGER NOT NULL CHECK (is_leaf IN (0, 1)),
    PRIMARY KEY (run_id, module)
);
CREATE TABLE IF NOT EXISTS import_edge (
    run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    source_module TEXT NOT NULL,
    target_module TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (run_id, source_module, target_module),
    FOREIGN KEY (run_id, source_module) REFERENCES import_module(run_id, module) ON DELETE CASCADE,
    FOREIGN KEY (run_id, target_module) REFERENCES import_module(run_id, module) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS community_run (
    run_id TEXT PRIMARY KEY REFERENCES run(run_id) ON DELETE CASCADE,
    algorithm TEXT NOT NULL,
    version TEXT NOT NULL,
    seed INTEGER NOT NULL,
    package TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS community (
    run_id TEXT NOT NULL REFERENCES community_run(run_id) ON DELETE CASCADE,
    community_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (run_id, community_id),
    UNIQUE (run_id, position)
);
CREATE TABLE IF NOT EXISTS community_member (
    run_id TEXT NOT NULL,
    community_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    module TEXT NOT NULL,
    PRIMARY KEY (run_id, community_id, position),
    FOREIGN KEY (run_id, community_id) REFERENCES community(run_id, community_id) ON DELETE CASCADE
);
"""


@dataclass(frozen=True)
class RunRecord:
    """Run identity and source provenance read from history."""

    run_id: str
    target_kind: str
    project_path: Path
    repository_url: str | None
    commit_sha: str | None
    head_sha: str | None
    dirty: bool
    configuration: dict[str, object]
    archive_path: Path


@dataclass(frozen=True)
class EvidenceSnapshot:
    """Typed producer evidence required by the current derived measurements."""

    run: RunRecord
    config: ScanConfig
    nodes: dict[str, PyanNode]
    pyan_edges: list[tuple[str, str]]
    vulture: list[VultureFinding]
    complexity: dict[Path, list[RadonBlock]]
    maintainability: dict[Path, float]
    private_access: list[PrivateAccess]
    import_modules: list[str]
    import_edges: list[tuple[str, str]]
    communities: list[list[str]]


def _dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return cast("dict[str, object]", value)


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("expected a JSON array")
    return cast("list[object]", value)


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _integer(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


@contextmanager
def connect(database: Path) -> Generator[sqlite3.Connection]:
    """Open a history database with its required integrity settings."""
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        version = cast(int, connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"history schema {version} is newer than supported schema {SCHEMA_VERSION}"
            )
        connection.executescript(_SCHEMA)
        if version == 0:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
        yield connection
    finally:
        connection.close()


def _relative_path(raw: str, project_path: str) -> str:
    path = Path(raw)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.relative_to(Path(project_path)).as_posix()
    except ValueError:
        return path.as_posix()


def _location(value: object) -> tuple[int, int]:
    location = cast("dict[str, object]", value) if isinstance(value, dict) else {}
    return _integer(location.get("row")), _integer(location.get("column"))


def _manifest(bundle: zipfile.ZipFile) -> dict[str, object]:
    return _dict(json.loads(bundle.read("manifest.json")))


def _verify_artifacts(bundle: zipfile.ZipFile, manifest: dict[str, object]) -> None:
    for raw in _list(manifest.get("artifacts")):
        artifact = _dict(raw)
        name = _text(artifact.get("name"))
        content = bundle.read(name)
        if len(content) != _integer(artifact.get("bytes")):
            raise RuntimeError(f"artifact size mismatch: {name}")
        digest = hashlib.sha256(content).hexdigest()
        if digest != _text(artifact.get("sha256")):
            raise RuntimeError(f"artifact checksum mismatch: {name}")


def _insert_run(
    connection: sqlite3.Connection,
    archive: Path,
    archive_sha256: str,
    manifest: dict[str, object],
) -> tuple[str, str]:
    run_id = _text(manifest.get("run_id"))
    target = _dict(manifest.get("target"))
    runtime = _dict(manifest.get("runtime"))
    identity = _text(target.get("project_identity"))
    kind = _text(target.get("kind"))
    connection.execute(
        "INSERT OR IGNORE INTO project(identity, target_kind) VALUES (?, ?)", (identity, kind)
    )
    project_id = cast(
        int,
        connection.execute(
            "SELECT project_id FROM project WHERE identity = ?", (identity,)
        ).fetchone()[0],
    )
    configuration = _dict(manifest.get("configuration"))
    connection.execute(
        """
        INSERT INTO run(
            run_id, project_id, target_kind, started_at_utc, status, project_path,
            repository_url, commit_sha, head_sha, dirty, configuration_json, python_version,
            platform, carpet_scan_version, archive_path, archive_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            project_id,
            kind,
            _text(manifest.get("started_at_utc")),
            _text(manifest.get("status")),
            _text(target.get("project_path")),
            target.get("repository_url"),
            target.get("commit_sha"),
            target.get("head_sha"),
            int(bool(target.get("dirty"))),
            json.dumps(configuration, sort_keys=True, separators=(",", ":")),
            _text(runtime.get("python")),
            _text(runtime.get("platform")),
            _text(runtime.get("carpet_scan")),
            str(archive.resolve()),
            archive_sha256,
        ),
    )
    for raw in _list(manifest.get("producers")):
        producer = _dict(raw)
        connection.execute(
            """
            INSERT INTO producer_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                _text(producer.get("producer")),
                _text(producer.get("version")),
                json.dumps(producer.get("operation"), separators=(",", ":")),
                _text(producer.get("status")),
                _text(producer.get("started_at_utc")),
                _text(producer.get("ended_at_utc")),
                _number(producer.get("duration_seconds")) or 0.0,
                producer.get("exit_code"),
                producer.get("error"),
            ),
        )
    for raw in _list(manifest.get("artifacts")):
        artifact = _dict(raw)
        connection.execute(
            "INSERT INTO artifact VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                _text(artifact.get("name")),
                _text(artifact.get("producer")),
                _text(artifact.get("media_type")),
                _integer(artifact.get("schema_version")),
                _integer(artifact.get("bytes")),
                _text(artifact.get("sha256")),
            ),
        )
    return run_id, _text(target.get("project_path"))


def _producer_complete(connection: sqlite3.Connection, run_id: str, producer: str) -> bool:
    row = connection.execute(
        "SELECT status FROM producer_run WHERE run_id = ? AND producer = ?", (run_id, producer)
    ).fetchone()
    return row is not None and row[0] == "complete"


def _ingest_pyan(
    connection: sqlite3.Connection,
    bundle: zipfile.ZipFile,
    run_id: str,
    project_path: str,
) -> None:
    if not _producer_complete(connection, run_id, "pyan"):
        return
    nodes, edges = parse_pyan(bundle.read("tools/pyan/graph.dot").decode())
    connection.executemany(
        "INSERT INTO pyan_node VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                run_id,
                node.node_id,
                node.qualname,
                node.module,
                node.name,
                node.kind,
                _relative_path(str(node.path), project_path) if node.path else None,
                node.lineno,
            )
            for node in nodes.values()
        ),
    )
    connection.executemany(
        "INSERT INTO pyan_edge VALUES (?, ?, ?, ?)",
        ((run_id, source, target, position) for position, (source, target) in enumerate(edges)),
    )


def _ingest_vulture(
    connection: sqlite3.Connection,
    bundle: zipfile.ZipFile,
    run_id: str,
    project_path: str,
) -> None:
    if not _producer_complete(connection, run_id, "vulture"):
        return
    content = bundle.read("tools/vulture/findings.txt").decode()
    raw_lines = [line for line in content.splitlines() if ": unused " in line]
    findings = parse_vulture(content)
    connection.executemany(
        "INSERT INTO vulture_finding VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                run_id,
                position,
                _relative_path(str(finding.path), project_path),
                finding.lineno,
                finding.kind,
                finding.name,
                finding.confidence,
                raw_lines[position] if position < len(raw_lines) else "",
            )
            for position, finding in enumerate(findings)
        ),
    )


def _insert_radon_block(
    connection: sqlite3.Connection,
    run_id: str,
    path: str,
    block: dict[str, object],
    position: int,
    parent_id: int | None,
) -> None:
    cursor = connection.execute(
        """
        INSERT INTO radon_cc_block(
            run_id, parent_block_id, position, path, block_type, name, line, column_offset,
            end_line, end_column_offset, complexity, rank
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            parent_id,
            position,
            path,
            _text(block.get("type")),
            _text(block.get("name")),
            _integer(block.get("lineno")),
            _integer(block.get("col_offset")),
            _integer(block.get("endline")),
            _integer(block.get("end_col_offset")),
            _integer(block.get("complexity")),
            _text(block.get("rank")),
        ),
    )
    block_id = cast(int, cursor.lastrowid)
    children = [*_list(block.get("closures", [])), *_list(block.get("methods", []))]
    for child_position, raw_child in enumerate(children):
        _insert_radon_block(connection, run_id, path, _dict(raw_child), child_position, block_id)


def _ingest_radon(connection: sqlite3.Connection, bundle: zipfile.ZipFile, run_id: str) -> None:
    if _producer_complete(connection, run_id, "radon_cc"):
        raw_cc = _dict(json.loads(bundle.read("tools/radon/cc.json")))
        for path, raw_blocks in raw_cc.items():
            for position, raw_block in enumerate(_list(raw_blocks)):
                _insert_radon_block(connection, run_id, path, _dict(raw_block), position, None)
    if _producer_complete(connection, run_id, "radon_mi"):
        raw_mi = _dict(json.loads(bundle.read("tools/radon/mi.json")))
        for path, raw_score in raw_mi.items():
            score = _dict(raw_score)
            connection.execute(
                "INSERT INTO radon_mi_file VALUES (?, ?, ?, ?, ?)",
                (
                    run_id,
                    path,
                    _number(score.get("mi")),
                    score.get("rank"),
                    score.get("error"),
                ),
            )


def _ingest_ruff(
    connection: sqlite3.Connection,
    bundle: zipfile.ZipFile,
    run_id: str,
    project_path: str,
) -> None:
    if not _producer_complete(connection, run_id, "ruff"):
        return
    entries = _list(json.loads(bundle.read("tools/ruff/findings.json")))
    for position, raw_entry in enumerate(entries):
        entry = _dict(raw_entry)
        start_row, start_column = _location(entry.get("location"))
        end_row, end_column = _location(entry.get("end_location"))
        cursor = connection.execute(
            """
            INSERT INTO ruff_finding(
                run_id, position, code, message, filename, start_row, start_column,
                end_row, end_column, noqa_row, url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                position,
                _text(entry.get("code")),
                _text(entry.get("message")),
                _relative_path(_text(entry.get("filename")), project_path),
                start_row,
                start_column,
                end_row,
                end_column,
                entry.get("noqa_row"),
                entry.get("url"),
            ),
        )
        finding_id = cast(int, cursor.lastrowid)
        raw_fix = entry.get("fix")
        if not isinstance(raw_fix, dict):
            continue
        fix = cast("dict[str, object]", raw_fix)
        connection.execute(
            "INSERT INTO ruff_fix VALUES (?, ?, ?)",
            (finding_id, fix.get("applicability"), fix.get("message")),
        )
        for edit_position, raw_edit in enumerate(_list(fix.get("edits", []))):
            edit = _dict(raw_edit)
            edit_start_row, edit_start_column = _location(edit.get("location"))
            edit_end_row, edit_end_column = _location(edit.get("end_location"))
            connection.execute(
                "INSERT INTO ruff_fix_edit VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    finding_id,
                    edit_position,
                    _text(edit.get("content")),
                    edit_start_row,
                    edit_start_column,
                    edit_end_row,
                    edit_end_column,
                ),
            )


def _ingest_grimp(connection: sqlite3.Connection, bundle: zipfile.ZipFile, run_id: str) -> None:
    if not _producer_complete(connection, run_id, "grimp"):
        return
    payload = _dict(json.loads(bundle.read("tools/grimp/import_graph.json")))
    connection.executemany(
        "INSERT INTO import_module VALUES (?, ?, ?, ?)",
        (
            (
                run_id,
                _text(module.get("name")),
                int(bool(module.get("is_package"))),
                int(bool(module.get("is_leaf"))),
            )
            for module in (_dict(raw) for raw in _list(payload.get("modules")))
        ),
    )
    connection.executemany(
        "INSERT INTO import_edge VALUES (?, ?, ?, ?)",
        (
            (run_id, _text(edge[0]), _text(edge[1]), position)
            for position, edge in enumerate(_list(raw) for raw in _list(payload.get("edges")))
            if len(edge) == 2
        ),
    )


def _ingest_networkx(connection: sqlite3.Connection, bundle: zipfile.ZipFile, run_id: str) -> None:
    if not _producer_complete(connection, run_id, "networkx"):
        return
    payload = _dict(json.loads(bundle.read("tools/networkx/communities.json")))
    producer = connection.execute(
        "SELECT version FROM producer_run WHERE run_id = ? AND producer = 'networkx'", (run_id,)
    ).fetchone()
    connection.execute(
        "INSERT INTO community_run VALUES (?, ?, ?, ?, ?)",
        (
            run_id,
            _text(payload.get("algorithm")),
            producer[0],
            _integer(payload.get("seed")),
            _text(payload.get("package")),
        ),
    )
    for position, raw_members in enumerate(_list(payload.get("communities"))):
        members = sorted(_text(member) for member in _list(raw_members))
        identity = hashlib.sha256("\0".join(members).encode()).hexdigest()
        connection.execute("INSERT INTO community VALUES (?, ?, ?)", (run_id, identity, position))
        connection.executemany(
            "INSERT INTO community_member VALUES (?, ?, ?, ?)",
            ((run_id, identity, index, module) for index, module in enumerate(members)),
        )


def ingest_archive(database: Path, archive: Path) -> str:
    """Verify and transactionally normalize one evidence archive."""
    resolved_archive = archive.resolve()
    archive_sha256 = hashlib.sha256(resolved_archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(resolved_archive) as bundle:
        if bundle.testzip() is not None:
            raise RuntimeError(f"corrupt evidence archive: {resolved_archive}")
        manifest = _manifest(bundle)
        if _integer(manifest.get("archive_version")) != SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported evidence archive version: {manifest.get('archive_version')}"
            )
        _verify_artifacts(bundle, manifest)
        with connect(database) as connection, connection:
            run_id, project_path = _insert_run(
                connection, resolved_archive, archive_sha256, manifest
            )
            _ingest_pyan(connection, bundle, run_id, project_path)
            _ingest_vulture(connection, bundle, run_id, project_path)
            _ingest_radon(connection, bundle, run_id)
            _ingest_ruff(connection, bundle, run_id, project_path)
            _ingest_grimp(connection, bundle, run_id)
            _ingest_networkx(connection, bundle, run_id)
    return run_id


def delete_run(database: Path, run_id: str) -> None:
    """Delete one normalized run while leaving its immutable archive untouched."""
    with connect(database) as connection, connection:
        connection.execute("DELETE FROM run WHERE run_id = ?", (run_id,))


def run_record(database: Path, run_id: str) -> RunRecord:
    """Read one run or raise when it is absent."""
    with connect(database) as connection:
        row = connection.execute("SELECT * FROM run WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown carpet run: {run_id}")
    return RunRecord(
        run_id=row["run_id"],
        target_kind=row["target_kind"],
        project_path=Path(row["project_path"]),
        repository_url=row["repository_url"],
        commit_sha=row["commit_sha"],
        head_sha=row["head_sha"],
        dirty=bool(row["dirty"]),
        configuration=_dict(json.loads(row["configuration_json"])),
        archive_path=Path(row["archive_path"]),
    )


def latest_run_id(database: Path) -> str:
    """Return the newest recorded run ID."""
    with connect(database) as connection:
        row = connection.execute(
            "SELECT run_id FROM run ORDER BY started_at_utc DESC, run_id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise ValueError(f"history contains no runs: {database}")
    return cast(str, row[0])


def _producer_versions(connection: sqlite3.Connection, run_id: str) -> ToolVersions:
    versions = {
        row["producer"]: row["version"]
        for row in connection.execute(
            "SELECT producer, version FROM producer_run WHERE run_id = ?", (run_id,)
        )
    }
    return ToolVersions(
        pyan3=versions.get("pyan", "unavailable"),
        vulture=versions.get("vulture", "unavailable"),
        radon=versions.get("radon_cc", "unavailable"),
        grimp=versions.get("grimp", "unavailable"),
        ruff=versions.get("ruff", "unavailable"),
        networkx=versions.get("networkx", "unavailable"),
    )


def _snapshot_nodes(connection: sqlite3.Connection, run_id: str, root: Path) -> dict[str, PyanNode]:
    return {
        row["node_id"]: PyanNode(
            node_id=row["node_id"],
            qualname=row["qualname"],
            module=row["module"],
            name=row["name"],
            kind=row["kind"],
            path=root / row["path"] if row["path"] else None,
            lineno=row["line"],
        )
        for row in connection.execute(
            "SELECT * FROM pyan_node WHERE run_id = ? ORDER BY node_id", (run_id,)
        )
    }


def _snapshot_vulture(
    connection: sqlite3.Connection, run_id: str, root: Path
) -> list[VultureFinding]:
    return [
        VultureFinding(
            path=root / row["path"],
            lineno=row["line"],
            kind=row["kind"],
            name=row["name"],
            confidence=row["confidence"],
        )
        for row in connection.execute(
            "SELECT * FROM vulture_finding WHERE run_id = ? ORDER BY position", (run_id,)
        )
    ]


def _snapshot_complexity(
    connection: sqlite3.Connection, run_id: str, root: Path
) -> dict[Path, list[RadonBlock]]:
    blocks: dict[Path, list[RadonBlock]] = {}
    rows = connection.execute(
        """
        SELECT path, name, line, complexity, rank
        FROM radon_cc_block
        WHERE run_id = ? AND parent_block_id IS NULL
        ORDER BY path, position
        """,
        (run_id,),
    )
    for row in rows:
        blocks.setdefault((root / row["path"]).resolve(), []).append(
            RadonBlock(
                name=row["name"],
                lineno=row["line"],
                complexity=row["complexity"],
                rank=row["rank"],
            )
        )
    return blocks


def _private_name(message: str) -> str:
    quoted = message.split("`")
    return quoted[1] if len(quoted) > 1 else message


def load_snapshot(database: Path, selected_run_id: str) -> EvidenceSnapshot:
    """Load producer evidence for derivation without reading live source or launching tools."""
    run = run_record(database, selected_run_id)
    root = run.project_path
    with connect(database) as connection:
        nodes = _snapshot_nodes(connection, selected_run_id, root)
        pyan_edges = [
            (row["source_node_id"], row["target_node_id"])
            for row in connection.execute(
                "SELECT * FROM pyan_edge WHERE run_id = ? ORDER BY position", (selected_run_id,)
            )
        ]
        vulture = _snapshot_vulture(connection, selected_run_id, root)
        complexity = _snapshot_complexity(connection, selected_run_id, root)
        maintainability = {
            (root / row["path"]).resolve(): row["mi"]
            for row in connection.execute(
                "SELECT path, mi FROM radon_mi_file WHERE run_id = ? AND mi IS NOT NULL",
                (selected_run_id,),
            )
        }
        private_access = [
            PrivateAccess(
                path=root / row["filename"],
                lineno=row["start_row"],
                code=row["code"],
                name=_private_name(row["message"]),
            )
            for row in connection.execute(
                "SELECT * FROM ruff_finding WHERE run_id = ? ORDER BY position", (selected_run_id,)
            )
        ]
        import_modules = [
            row["module"]
            for row in connection.execute(
                "SELECT module FROM import_module WHERE run_id = ? AND is_leaf = 1 ORDER BY module",
                (selected_run_id,),
            )
        ]
        import_edges = [
            (row["source_module"], row["target_module"])
            for row in connection.execute(
                "SELECT * FROM import_edge WHERE run_id = ? ORDER BY position", (selected_run_id,)
            )
            if row["source_module"] in import_modules and row["target_module"] in import_modules
        ]
        communities = [
            [
                member["module"]
                for member in connection.execute(
                    """
                    SELECT module FROM community_member
                    WHERE run_id = ? AND community_id = ? ORDER BY position
                    """,
                    (selected_run_id, community["community_id"]),
                )
            ]
            for community in connection.execute(
                "SELECT * FROM community WHERE run_id = ? ORDER BY position", (selected_run_id,)
            )
        ]
        versions = _producer_versions(connection, selected_run_id)
    configuration = run.configuration
    src = Path(_text(configuration.get("src")))
    tests_text = configuration.get("tests")
    tests = Path(tests_text) if isinstance(tests_text, str) else None
    surface_skip = tuple(_text(item) for item in _list(configuration.get("surface_skip", [])))
    config = ScanConfig(
        src=src,
        tests=tests,
        package=_text(configuration.get("package")),
        top=12,
        warn_depth=_integer(configuration.get("warn_depth"), 3),
        modules=len({node.module for node in nodes.values()}),
        versions=versions,
        surface_skip=surface_skip,
        skipped=0,
        ignore_decorators=_text(configuration.get("ignore_decorators")),
    )
    return EvidenceSnapshot(
        run=run,
        config=config,
        nodes=nodes,
        pyan_edges=pyan_edges,
        vulture=vulture,
        complexity=complexity,
        maintainability=maintainability,
        private_access=private_access,
        import_modules=import_modules,
        import_edges=import_edges,
        communities=communities,
    )
