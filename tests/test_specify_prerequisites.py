# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Spec Kit prerequisite checker."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".specify" / "scripts" / "bash"


def _assert_safe_sources() -> None:
    """Prevent invoking path scripts whose dependencies use evaluation."""
    for path in (
        SCRIPTS / "check-prerequisites.sh",
        SCRIPTS / "setup-plan.sh",
        SCRIPTS / "update-agent-context.sh",
        SCRIPTS / "common.sh",
    ):
        assert not re.search(r"(?m)^\s*eval(?:\s|$)", path.read_text())


def test_path_scripts_do_not_evaluate_generated_shell() -> None:
    """Keep path resolution free of dynamically evaluated assignments."""
    _assert_safe_sources()
    updater = (SCRIPTS / "update-agent-context.sh").read_text()
    assert (
        'set_feature_paths || { echo "ERROR: Failed to resolve feature paths" '
        ">&2; exit 1; }"
    ) in updater


@pytest.fixture
def feature_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a Git-backed feature with literal shell-special path characters."""
    _assert_safe_sources()
    root = tmp_path / "repo 'spaces' [group]"
    scripts = root / ".specify" / "scripts" / "bash"
    scripts.mkdir(parents=True)
    for name in ("common.sh", "check-prerequisites.sh", "setup-plan.sh"):
        shutil.copy2(SCRIPTS / name, scripts / name)
    template = root / ".specify" / "templates" / "plan-template.md"
    template.parent.mkdir(parents=True)
    template.write_text("Plan template\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    feature = root / "specs" / "010-stay 'date' & more"
    feature.mkdir(parents=True)
    for name in ("spec.md", "plan.md", "tasks.md"):
        (feature / name).write_text("feature\n", encoding="utf-8")
    return root, scripts / "check-prerequisites.sh", feature


def _run(
    repo: Path,
    checker: Path,
    *args: str,
    feature: str = "010-example",
) -> subprocess.CompletedProcess[str]:
    """Run the verified checker against an isolated fixture."""
    return subprocess.run(
        ["bash", str(checker), *args],
        cwd=repo,
        env={**os.environ, "SPECIFY_FEATURE": feature},
        capture_output=True,
        text=True,
        check=False,
    )


def test_json_and_paths_preserve_literal_names(
    feature_repo: tuple[Path, Path, Path],
) -> None:
    """Resolve and report exact paths without reinterpreting their characters."""
    repo, checker, feature = feature_repo
    result = _run(repo, checker, "--json", "--require-tasks", "--include-tasks")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "FEATURE_DIR": str(feature),
        "AVAILABLE_DOCS": ["tasks.md"],
    }

    paths = _run(repo, checker, "--json", "--paths-only")
    assert paths.returncode == 0, paths.stderr
    data = json.loads(paths.stdout)
    assert data == {
        "REPO_ROOT": str(repo),
        "BRANCH": "010-example",
        "FEATURE_DIR": str(feature),
        "FEATURE_SPEC": str(feature / "spec.md"),
        "IMPL_PLAN": str(feature / "plan.md"),
        "TASKS": str(feature / "tasks.md"),
    }
    text = _run(repo, checker, "--include-tasks")
    assert text.returncode == 0, text.stderr
    assert f"FEATURE_DIR:{feature}" in text.stdout
    assert "tasks.md" in text.stdout


@pytest.mark.parametrize("json_mode", [False, True])
def test_setup_plan_preserves_literal_paths_and_template(
    feature_repo: tuple[Path, Path, Path], json_mode: bool
) -> None:
    """Create the plan from its template without reinterpreting path characters."""
    repo, _, feature = feature_repo
    script = repo / ".specify" / "scripts" / "bash" / "setup-plan.sh"
    result = _run(repo, script, *(["--json"] if json_mode else []))
    assert result.returncode == 0, result.stderr
    assert (feature / "plan.md").read_text(encoding="utf-8") == "Plan template\n"

    if json_mode:
        assert json.loads(result.stdout.splitlines()[-1]) == {
            "FEATURE_SPEC": str(feature / "spec.md"),
            "IMPL_PLAN": str(feature / "plan.md"),
            "SPECS_DIR": str(feature),
            "BRANCH": "010-example",
            "HAS_GIT": "true",
        }
    else:
        assert f"Copied plan template to {feature / 'plan.md'}" in result.stdout
        assert f"FEATURE_SPEC: {feature / 'spec.md'}" in result.stdout
        assert f"IMPL_PLAN: {feature / 'plan.md'}" in result.stdout


def test_setup_plan_path_resolution_failure_is_reported(
    feature_repo: tuple[Path, Path, Path],
) -> None:
    """Ambiguous feature paths fail before creating a plan."""
    repo, _, feature = feature_repo
    script = repo / ".specify" / "scripts" / "bash" / "setup-plan.sh"
    (feature / "plan.md").unlink()
    (repo / "specs" / "010-another").mkdir()

    result = _run(repo, script, "--json")

    assert result.returncode != 0
    assert "Multiple spec directories" in result.stderr
    assert "ERROR: Failed to resolve feature paths" in result.stderr
    assert not (feature / "plan.md").exists()


@pytest.mark.parametrize(
    ("name", "message"),
    [("plan.md", "plan.md not found"), ("tasks.md", "tasks.md not found")],
)
def test_missing_required_documents_fail(
    feature_repo: tuple[Path, Path, Path], name: str, message: str
) -> None:
    """Missing required files retain their errors and nonzero exits."""
    repo, checker, feature = feature_repo
    (feature / name).unlink()
    result = _run(repo, checker, "--require-tasks")
    assert result.returncode != 0
    assert message in result.stderr


def test_paths_only_does_not_require_a_plan(
    feature_repo: tuple[Path, Path, Path],
) -> None:
    """Paths-only mode resolves paths without requiring feature documents."""
    repo, checker, feature = feature_repo
    (feature / "plan.md").unlink()
    result = _run(repo, checker, "--paths-only")
    assert result.returncode == 0, result.stderr
    assert f"IMPL_PLAN: {feature / 'plan.md'}" in result.stdout


def test_legacy_path_output_is_unchanged(
    feature_repo: tuple[Path, Path, Path],
) -> None:
    """The existing path-output helper remains available to other callers."""
    repo, checker, feature = feature_repo
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; get_feature_paths',
            "bash",
            str(checker.parent / "common.sh"),
        ],
        cwd=repo,
        env={**os.environ, "SPECIFY_FEATURE": "010-example"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    entries = dict(
        shlex.split(line)[0].split("=", 1) for line in result.stdout.splitlines()
    )
    assert entries["FEATURE_DIR"] == str(feature)
    assert entries["FEATURE_SPEC"] == str(feature / "spec.md")
    assert entries["HAS_GIT"] == "true"


def test_invalid_branch_and_duplicate_prefix_fail(
    feature_repo: tuple[Path, Path, Path],
) -> None:
    """Branch validation and ambiguous feature directories remain errors."""
    repo, checker, _ = feature_repo
    invalid = _run(repo, checker, "--json", feature="not-a-feature")
    assert invalid.returncode != 0
    assert "Not on a feature branch" in invalid.stderr

    (repo / "specs" / "010-another").mkdir()
    duplicate = _run(repo, checker, "--json")
    assert duplicate.returncode != 0
    assert "Multiple spec directories" in duplicate.stderr
    assert "Failed to resolve feature paths" in duplicate.stderr


def test_unknown_option_fails(feature_repo: tuple[Path, Path, Path]) -> None:
    """Unknown command-line options are rejected."""
    repo, checker, _ = feature_repo
    result = _run(repo, checker, "--not-an-option")
    assert result.returncode != 0
    assert "Unknown option" in result.stderr
