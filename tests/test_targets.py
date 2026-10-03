"""Typed local and GitHub scan target resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from carpet_scan.targets import canonical_github_url, local_target, project_layout


def git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, capture_output=True, text=True, check=True)


def test_github_url_has_one_canonical_project_identity() -> None:
    expected = ("https://github.com/pypa/sampleproject.git", "pypa", "sampleproject")

    assert canonical_github_url("https://github.com/pypa/sampleproject") == expected
    assert canonical_github_url("git@github.com:pypa/sampleproject.git") == expected


@pytest.mark.parametrize(
    "url",
    ["https://gitlab.com/pypa/sampleproject", "https://github.com/pypa", "file:///tmp/repo"],
)
def test_github_url_rejects_other_target_shapes(url: str) -> None:
    with pytest.raises(ValueError):
        canonical_github_url(url)


def test_local_target_records_dirty_state_without_copying_it(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "src" / "pkg"
    package.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='pkg'\nversion='0.1'\n")
    (package / "__init__.py").write_text("")
    git(project, "init")
    git(project, "config", "user.email", "test@example.com")
    git(project, "config", "user.name", "Test")
    git(project, "add", ".")
    git(project, "commit", "-m", "initial")
    clean = local_target(project)

    (package / "__init__.py").write_text("VALUE = 1\n")
    dirty = local_target(project)

    assert clean.head_sha is not None and not clean.dirty
    assert dirty.head_sha == clean.head_sha and dirty.dirty
    assert project_layout(project).package == "pkg"
