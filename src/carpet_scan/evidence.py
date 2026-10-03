"""Capture diagnostic outputs and seal them as immutable ZIP evidence."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

import grimp
import networkx as nx

from carpet_scan.measure import leaf_imports
from carpet_scan.model import COMMUNITY_SEED, VULTURE_MIN_CONFIDENCE, ScanConfig
from carpet_scan.targets import ProjectLayout
from carpet_scan.tools import tool_command, tool_versions

ARCHIVE_VERSION = 1
ZIP_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class Artifact:
    """One exact output retained in an evidence archive."""

    name: str
    producer: str
    media_type: str
    schema_version: int
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class ProducerResult:
    """One diagnostic producer invocation and all bytes it emitted."""

    producer: str
    version: str
    operation: list[str]
    status: str
    started_at_utc: str
    ended_at_utc: str
    duration_seconds: float
    exit_code: int | None
    artifacts: list[Artifact]
    error: str | None = None


@dataclass(frozen=True)
class TargetRecord:
    """Source identity recorded for one acquisition run."""

    kind: str
    project_identity: str
    project_path: str
    repository_url: str | None
    commit_sha: str | None
    head_sha: str | None
    dirty: bool


@dataclass(frozen=True)
class SealedRun:
    """A published run directory and its verified archive."""

    run_id: str
    directory: Path
    archive: Path
    archive_sha256: str


def _version(distribution: str) -> str:
    try:
        return package_version(distribution)
    except PackageNotFoundError:
        return "unknown"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _run_id(started: datetime, commit_sha: str | None) -> str:
    timestamp = started.strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{timestamp}_{commit_sha or 'none'}"


def _artifact(
    name: str,
    producer: str,
    media_type: str,
    content: bytes,
    schema_version: int = 1,
) -> Artifact:
    return Artifact(
        name=name,
        producer=producer,
        media_type=media_type,
        schema_version=schema_version,
        content=content,
    )


def _capture_command(
    producer: str,
    version: str,
    command: list[str],
    cwd: Path,
    stdout_name: str,
    stdout_type: str,
    tolerated: tuple[int, ...],
    require_stdout: bool = False,
) -> ProducerResult:
    started = _utc_now()
    before = time.monotonic()
    result = subprocess.run(command, cwd=cwd, capture_output=True, check=False)
    ended = _utc_now()
    status = (
        "complete"
        if result.returncode in tolerated and (result.stdout.strip() or not require_stdout)
        else "failed"
    )
    failure_text = result.stderr or b"producer emitted no usable output"
    error = None if status == "complete" else failure_text.decode(errors="replace")[-600:]
    artifacts = [
        _artifact(stdout_name, producer, stdout_type, result.stdout),
        _artifact(f"tools/{producer}/stderr.txt", producer, "text/plain", result.stderr),
    ]
    return ProducerResult(
        producer=producer,
        version=version,
        operation=command,
        status=status,
        started_at_utc=_iso(started),
        ended_at_utc=_iso(ended),
        duration_seconds=time.monotonic() - before,
        exit_code=result.returncode,
        artifacts=artifacts,
        error=error,
    )


def _relative(path: Path, project: Path) -> str:
    return path.resolve().relative_to(project.resolve()).as_posix()


def _capture_cli_tools(layout: ProjectLayout, config: ScanConfig) -> list[ProducerResult]:
    src = _relative(layout.src, layout.project)
    tests = _relative(layout.tests, layout.project) if layout.tests else None
    targets = [src, *([tests] if tests else [])]
    versions = config.versions
    return [
        _capture_command(
            "pyan",
            versions.pyan3,
            tool_command("pyan3", *targets, "--root", ".", "--uses", "--no-defines", "--dot"),
            layout.project,
            "tools/pyan/graph.dot",
            "text/vnd.graphviz",
            (0,),
            require_stdout=True,
        ),
        _capture_command(
            "vulture",
            versions.vulture,
            tool_command(
                "vulture",
                *targets,
                "--min-confidence",
                str(VULTURE_MIN_CONFIDENCE),
                "--ignore-decorators",
                config.ignore_decorators,
            ),
            layout.project,
            "tools/vulture/findings.txt",
            "text/plain",
            (0, 1, 3),
        ),
        _capture_command(
            "radon_cc",
            versions.radon,
            tool_command("radon", "cc", src, "-j"),
            layout.project,
            "tools/radon/cc.json",
            "application/json",
            (0, 1, 3),
        ),
        _capture_command(
            "radon_mi",
            versions.radon,
            tool_command("radon", "mi", src, "-j"),
            layout.project,
            "tools/radon/mi.json",
            "application/json",
            (0, 1, 3),
        ),
        _capture_command(
            "ruff",
            versions.ruff,
            tool_command(
                "ruff",
                "check",
                *targets,
                "--select",
                "SLF001,PLC2701",
                "--preview",
                "--output-format",
                "json",
                "--no-cache",
            ),
            layout.project,
            "tools/ruff/findings.json",
            "application/json",
            (0, 1),
        ),
    ]


def _api_result(
    producer: str,
    version: str,
    operation: list[str],
    artifact_name: str,
    payload: object,
    started: datetime,
    before: float,
) -> ProducerResult:
    content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ended = _utc_now()
    return ProducerResult(
        producer=producer,
        version=version,
        operation=operation,
        status="complete",
        started_at_utc=_iso(started),
        ended_at_utc=_iso(ended),
        duration_seconds=time.monotonic() - before,
        exit_code=None,
        artifacts=[_artifact(artifact_name, producer, "application/json", content)],
    )


def _failed_api_result(
    producer: str,
    version: str,
    operation: list[str],
    error: Exception,
    started: datetime,
    before: float,
) -> ProducerResult:
    detail = str(error)
    return ProducerResult(
        producer=producer,
        version=version,
        operation=operation,
        status="failed",
        started_at_utc=_iso(started),
        ended_at_utc=_iso(_utc_now()),
        duration_seconds=time.monotonic() - before,
        exit_code=None,
        artifacts=[
            _artifact(
                f"tools/{producer}/stderr.txt",
                producer,
                "text/plain",
                detail.encode(),
            )
        ],
        error=detail[-600:],
    )


def _unavailable_api_result(
    producer: str, version: str, operation: list[str], detail: str
) -> ProducerResult:
    now = _iso(_utc_now())
    return ProducerResult(
        producer=producer,
        version=version,
        operation=operation,
        status="unavailable",
        started_at_utc=now,
        ended_at_utc=now,
        duration_seconds=0.0,
        exit_code=None,
        artifacts=[
            _artifact(f"tools/{producer}/stderr.txt", producer, "text/plain", detail.encode())
        ],
        error=detail,
    )


def _grimp_payload(graph: grimp.ImportGraph) -> dict[str, object]:
    modules = sorted(graph.modules)
    packages = {
        parent for module in modules for parent in [module.rsplit(".", 1)[0]] if parent in modules
    }
    edges = [
        [module, imported]
        for module in modules
        for imported in sorted(graph.find_modules_directly_imported_by(module))
        if imported in graph.modules and imported != module
    ]
    return {
        "modules": [
            {"name": module, "is_package": module in packages, "is_leaf": module not in packages}
            for module in modules
        ],
        "edges": edges,
    }


def _community_payload(
    modules: list[str], edges: list[tuple[str, str]], package: str
) -> dict[str, object]:
    graph: nx.Graph[str] = nx.Graph()
    graph.add_nodes_from(modules)
    for source, target in edges:
        weight = graph.edges.get((source, target), {}).get("weight", 0)
        graph.add_edge(source, target, weight=weight + 1)
    found = nx.community.louvain_communities(graph, seed=COMMUNITY_SEED)
    communities = [sorted(members) for members in found]
    communities.sort(key=lambda members: (-len(members), members))
    return {
        "algorithm": "louvain_communities",
        "seed": COMMUNITY_SEED,
        "package": package,
        "communities": communities,
    }


def capture_evidence(layout: ProjectLayout, config: ScanConfig) -> list[ProducerResult]:
    """Run every producer once and return raw, independently statused evidence."""
    producers = _capture_cli_tools(layout, config)
    started = _utc_now()
    before = time.monotonic()
    source_path = str(layout.src)
    sys.path.insert(0, source_path)
    importlib.invalidate_caches()
    graph: grimp.ImportGraph | None = None
    try:
        graph = grimp.build_graph(layout.package)
        grimp_result = _api_result(
            "grimp",
            config.versions.grimp,
            ["grimp.build_graph", layout.package],
            "tools/grimp/import_graph.json",
            _grimp_payload(graph),
            started,
            before,
        )
        producers.append(grimp_result)
    except (RuntimeError, ValueError, ImportError) as error:
        producers.append(
            _failed_api_result(
                "grimp",
                config.versions.grimp,
                ["grimp.build_graph", layout.package],
                error,
                started,
                before,
            )
        )
    finally:
        sys.path.remove(source_path)
        importlib.invalidate_caches()
    if graph is None:
        producers.append(
            _unavailable_api_result(
                "networkx",
                config.versions.networkx,
                ["networkx.community.louvain_communities", f"seed={COMMUNITY_SEED}"],
                "unavailable because Grimp did not produce an import graph",
            )
        )
        return producers
    leaves, edges = leaf_imports(graph)
    networkx_started = _utc_now()
    networkx_before = time.monotonic()
    try:
        payload = _community_payload(leaves, edges, layout.package)
        producers.append(
            _api_result(
                "networkx",
                config.versions.networkx,
                ["networkx.community.louvain_communities", f"seed={COMMUNITY_SEED}"],
                "tools/networkx/communities.json",
                payload,
                networkx_started,
                networkx_before,
            )
        )
    except (RuntimeError, ValueError, nx.NetworkXException) as error:
        producers.append(
            _failed_api_result(
                "networkx",
                config.versions.networkx,
                ["networkx.community.louvain_communities", f"seed={COMMUNITY_SEED}"],
                error,
                networkx_started,
                networkx_before,
            )
        )
    return producers


def _artifact_record(artifact: Artifact) -> dict[str, object]:
    return {
        "name": artifact.name,
        "producer": artifact.producer,
        "media_type": artifact.media_type,
        "schema_version": artifact.schema_version,
        "bytes": len(artifact.content),
        "sha256": artifact.sha256,
    }


def make_manifest(
    run_id: str,
    started_at_utc: str,
    target: TargetRecord,
    config: ScanConfig,
    producers: list[ProducerResult],
) -> dict[str, object]:
    """Create canonical manifest content for a captured run."""
    completed = sum(item.status == "complete" for item in producers)
    status = (
        "complete" if completed == len(producers) else "failed" if completed == 0 else "partial"
    )
    artifacts = [artifact for producer in producers for artifact in producer.artifacts]
    return {
        "archive_version": ARCHIVE_VERSION,
        "run_id": run_id,
        "started_at_utc": started_at_utc,
        "status": status,
        "target": asdict(target),
        "configuration": {
            "src": _relative(config.src, Path(target.project_path)),
            "tests": (
                _relative(config.tests, Path(target.project_path))
                if config.tests is not None
                else None
            ),
            "package": config.package,
            "warn_depth": config.warn_depth,
            "surface_skip": list(config.surface_skip),
            "ignore_decorators": config.ignore_decorators,
        },
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "carpet_scan": _version("carpet-scan"),
        },
        "producers": [
            {key: value for key, value in asdict(producer).items() if key != "artifacts"}
            for producer in producers
        ],
        "artifacts": [_artifact_record(artifact) for artifact in artifacts],
    }


def _write_zip(archive: Path, manifest: dict[str, object], producers: list[ProducerResult]) -> None:
    entries = {
        artifact.name: artifact.content for producer in producers for artifact in producer.artifacts
    }
    entries["manifest.json"] = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            bundle.writestr(info, entries[name])


def seal_evidence(
    carpet_root: Path,
    started: datetime,
    target: TargetRecord,
    config: ScanConfig,
    producers: list[ProducerResult],
) -> SealedRun:
    """Publish a checksummed append-only run archive atomically."""
    runs = carpet_root.resolve() / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    run_id = _run_id(started, target.commit_sha if target.kind == "github" else None)
    final = runs / run_id
    if final.exists():
        raise FileExistsError(f"evidence run already exists: {final}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{run_id}.", dir=runs))
    archive = temporary / "evidence.zip"
    try:
        manifest = make_manifest(run_id, _iso(started), target, config, producers)
        _write_zip(archive, manifest, producers)
        with zipfile.ZipFile(archive) as bundle:
            corrupt = bundle.testzip()
            if corrupt is not None:
                raise RuntimeError(f"evidence archive verification failed at {corrupt}")
        os.replace(temporary, final)
    except BaseException:
        for child in temporary.iterdir():
            child.unlink()
        temporary.rmdir()
        raise
    published = final / "evidence.zip"
    return SealedRun(
        run_id=run_id,
        directory=final,
        archive=published,
        archive_sha256=hashlib.sha256(published.read_bytes()).hexdigest(),
    )


def default_config(layout: ProjectLayout) -> ScanConfig:
    """The effective diagnostic configuration for a project acquisition."""
    from carpet_scan.model import IGNORED_DECORATORS, SURFACE_SKIP

    return ScanConfig(
        src=layout.src,
        tests=layout.tests,
        package=layout.package,
        top=12,
        warn_depth=3,
        modules=0,
        versions=tool_versions(),
        surface_skip=SURFACE_SKIP,
        skipped=0,
        ignore_decorators=IGNORED_DECORATORS,
    )
