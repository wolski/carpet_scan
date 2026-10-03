"""Resolve local folders and immutable GitHub revisions into scan targets."""

from __future__ import annotations

import re
import subprocess
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class ProjectLayout:
    """The source roots and import package discovered in a Python project."""

    project: Path
    src: Path
    tests: Path | None
    package: str


@dataclass(frozen=True)
class LocalTarget:
    """A mutable project folder supplied by the caller."""

    project: Path
    head_sha: str | None
    dirty: bool


@dataclass(frozen=True)
class GitHubTarget:
    """An immutable GitHub revision in a managed project directory."""

    repository_url: str
    commit_sha: str
    managed_project: Path
    checkout: Path


def _git(*args: str, cwd: Path | None = None) -> str:
    command = ["git", *args]
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{' '.join(command)} failed: {detail[-600:]}")
    return result.stdout.strip()


def canonical_github_url(url: str) -> tuple[str, str, str]:
    """Return canonical HTTPS URL, organization, and project for a GitHub repository."""
    candidate = url.strip()
    if candidate.startswith("git@github.com:"):
        candidate = f"https://github.com/{candidate.removeprefix('git@github.com:')}"
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise ValueError("GitHub repository must be an https://github.com/... URL or git@ URL")
    pieces = [piece for piece in parsed.path.removesuffix(".git").split("/") if piece]
    if len(pieces) != 2:
        raise ValueError("GitHub repository URL must identify exactly one organization and project")
    organization, project = pieces
    safe = re.compile(r"^[A-Za-z0-9_.-]+$")
    if not safe.fullmatch(organization) or not safe.fullmatch(project):
        raise ValueError("GitHub organization and project contain unsupported characters")
    return f"https://github.com/{organization}/{project}.git", organization, project


def local_target(project: Path) -> LocalTarget:
    """Inspect a local project without preserving its uncommitted source state."""
    resolved = project.resolve()
    if not resolved.is_dir():
        raise ValueError(f"project folder does not exist: {resolved}")
    if not (resolved / "pyproject.toml").is_file():
        raise ValueError(f"project has no pyproject.toml: {resolved}")
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=resolved,
        capture_output=True,
        text=True,
        check=False,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return LocalTarget(project=resolved, head_sha=None, dirty=False)
    head = _git("rev-parse", "HEAD", cwd=resolved)
    dirty = bool(_git("status", "--porcelain", cwd=resolved))
    return LocalTarget(project=resolved, head_sha=head, dirty=dirty)


def project_layout(project: Path) -> ProjectLayout:
    """Discover the conventional source, test, and single import-package roots."""
    resolved = project.resolve()
    source = resolved / "src" if (resolved / "src").is_dir() else resolved
    tests = resolved / "tests" if (resolved / "tests").is_dir() else None
    ignored = {"build", "dist", "tests", "test", ".carpet"}
    candidates = sorted(
        child.name
        for child in source.iterdir()
        if child.is_dir()
        and not child.name.startswith((".", "_"))
        and child.name not in ignored
        and ((child / "__init__.py").is_file() or (child / "py.typed").is_file())
    )
    if len(candidates) != 1:
        raise ValueError(
            f"cannot infer one import package under {source}: found {candidates or 'nothing'}"
        )
    return ProjectLayout(project=resolved, src=source, tests=tests, package=candidates[0])


def _validate_managed_path(workdir: Path, managed: Path) -> None:
    root = workdir.resolve()
    candidate = managed.resolve()
    if candidate.parent != root or candidate == root:
        raise RuntimeError(f"managed project escaped the requested workdir: {candidate}")


@contextmanager
def github_target(url: str, commit: str, workdir: Path) -> Generator[GitHubTarget]:
    """Prepare and later remove an isolated checkout of one GitHub revision."""
    canonical, organization, project = canonical_github_url(url)
    root = workdir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    managed = root / f"{organization}_{project}"
    _validate_managed_path(root, managed)
    managed.mkdir(exist_ok=True)
    cache = managed / ".git-cache"
    carpet = managed / ".carpet"
    checkouts = carpet / "checkouts"
    checkouts.mkdir(parents=True, exist_ok=True)

    if cache.exists():
        origin = _git("--git-dir", str(cache), "remote", "get-url", "origin")
        known, _, _ = canonical_github_url(origin)
        if known != canonical:
            raise RuntimeError(f"managed checkout belongs to {known}, not {canonical}")
    else:
        _git("clone", "--bare", canonical, str(cache))

    _git("--git-dir", str(cache), "fetch", "--no-tags", "origin", commit)
    resolved_commit = _git("--git-dir", str(cache), "rev-parse", "FETCH_HEAD^{commit}")
    checkout = checkouts / f"{resolved_commit}-{uuid.uuid4().hex}"
    if checkout.exists():
        raise RuntimeError(f"managed checkout already exists: {checkout}")
    _git("--git-dir", str(cache), "worktree", "add", "--detach", str(checkout), resolved_commit)
    try:
        if not (checkout / "pyproject.toml").is_file():
            raise ValueError(
                f"GitHub revision has no pyproject.toml: {canonical}@{resolved_commit}"
            )
        yield GitHubTarget(
            repository_url=canonical,
            commit_sha=resolved_commit,
            managed_project=managed,
            checkout=checkout,
        )
    finally:
        _git("--git-dir", str(cache), "worktree", "remove", str(checkout))
