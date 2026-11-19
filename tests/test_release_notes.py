# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for authored release-note selection and validation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RENDERER_PATH = REPO_ROOT / "scripts" / "render_release_notes.py"
SPEC = importlib.util.spec_from_file_location("render_release_notes", RENDERER_PATH)
assert SPEC is not None and SPEC.loader is not None
rrn = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rrn
SPEC.loader.exec_module(rrn)


class FakeLoader:
    """Provide parsed notes without invoking reno."""

    def __init__(self, content: dict[str, dict[str, Any]]) -> None:
        """Store note content by filename."""
        self.content = content

    @property
    def versions(self) -> list[str]:
        """Return the sole synthetic version."""
        return ["*working-copy*"]

    def __getitem__(self, _version: str) -> list[tuple[str, str | None]]:
        """Return all synthetic note filenames."""
        return [(name, "sha") for name in self.content]

    def parse_note_file(self, filename: str, _sha: str | None) -> dict[str, Any]:
        """Return one synthetic note."""
        return self.content[filename]


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        (None, ["*working-copy*", "1.0.0-1"]),
        ("v1.0.0", ["1.0.0"]),
        ("1.0.0", ["1.0.0"]),
        ("v9.9.9", []),
    ],
)
def test_select_versions_normalizes_release_tag_prefix(
    version: str | None, expected: list[str]
) -> None:
    """Unreleased selection stops at a tagged version with or without ``v``."""
    versions = ["*working-copy*", "1.0.0-1", "1.0.0", "0.9.0"]
    tags = {"v1.0.0", "v0.9.0"}

    assert rrn.select_versions(versions, tags, version) == expected


def test_collect_notes_rejects_malformed_section_data() -> None:
    """Invalid section types must not be silently omitted from release notes."""
    loader = FakeLoader({"bad.yaml": {"features": {"unexpected": "mapping"}}})

    with pytest.raises(ValueError, match=r"bad.yaml.*features"):
        rrn.collect_notes(loader, ["*working-copy*"], ["features"], "prelude")


def test_collect_notes_rejects_unknown_section_names() -> None:
    """Typographical section names must not disappear from the release body."""
    loader = FakeLoader({"typo.yaml": {"unknown_section": ["Feature text."]}})

    with pytest.raises(ValueError, match=r"typo.yaml.*unknown_section"):
        rrn.collect_notes(loader, ["*working-copy*"], ["features"], "prelude")


def test_validate_upgrade_note_requires_operator_guidance(tmp_path: Path) -> None:
    """Breaking-change notes need non-empty upgrade instructions."""
    empty = tmp_path / "empty.yaml"
    empty.write_text("---\nfeatures:\n  - New feature.\n", encoding="utf-8")
    blank = tmp_path / "blank.yaml"
    blank.write_text("---\nupgrade:\n  - '  '\n", encoding="utf-8")
    placeholder = tmp_path / "placeholder.yaml"
    placeholder.write_text(
        "---\nupgrade:\n  - Describe the required operator action and its impact.\n",
        encoding="utf-8",
    )
    complete = tmp_path / "complete.yaml"
    complete.write_text(
        "---\nupgrade:\n  - Set Reservations API to legacy to roll back.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"empty.yaml.*upgrade"):
        rrn.validate_upgrade_notes([empty])
    with pytest.raises(ValueError, match=r"blank.yaml.*upgrade"):
        rrn.validate_upgrade_notes([blank])
    with pytest.raises(ValueError, match=r"placeholder.yaml.*guidance"):
        rrn.validate_upgrade_notes([placeholder])
    rrn.validate_upgrade_notes([complete])


def test_validate_upgrade_notes_rejects_no_added_notes() -> None:
    """A breaking pull request cannot pass without an added snippet."""
    with pytest.raises(ValueError, match="must add"):
        rrn.validate_upgrade_notes([])


def test_added_note_discovery_preserves_newline_filenames(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Git's NUL-delimited paths keep arbitrary filenames intact."""
    expected = Path("releasenotes/notes/first\nsecond.yaml")
    monkeypatch.setattr(
        rrn.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=f"{expected}\0".encode()),
    )

    assert rrn.added_release_note_paths(tmp_path, "base", "head") == [expected]


def test_release_drafter_config_preserves_template_and_embeds_notes() -> None:
    """Rendered upgrade notes precede changes without replacing categories."""
    base = """---
categories:
  - title: Breaking
    when:
      label: breaking-change
header: |
  Download badge
template: |
  $CHANGES

  ## Links
"""

    generated = yaml.safe_load(
        rrn.build_drafter_config(base, "### Upgrade Notes\n\n- Roll back.")
    )

    assert generated["header"] == (
        "Download badge\n\n### Upgrade Notes\n\n- Roll back.\n\n"
    )
    assert generated["categories"] == [
        {"title": "Breaking", "when": {"label": "breaking-change"}}
    ]
    assert "$CHANGES" in generated["template"]
    assert "## Links" in generated["template"]


def test_main_rejects_an_explicit_version_that_is_not_a_tag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing release tag must fail instead of silently rendering nothing."""
    monkeypatch.setattr(rrn, "git_tags", lambda _root: {"v1.0.0"})

    assert rrn.main(["--version", "v2.0.0"]) == 2
    assert "not a git tag" in capsys.readouterr().err
