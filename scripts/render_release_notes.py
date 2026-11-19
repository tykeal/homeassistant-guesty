#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Render reno release notes as Markdown for GitHub release drafts."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

RELNOTES_DIR = "releasenotes"
WORKING_COPY = "*working-copy*"


class NoteLoader(Protocol):
    """The subset of ``reno.loader.Loader`` used by this renderer."""

    @property
    def versions(self) -> list[str]:
        """Return versions with notes, newest first."""

    def __getitem__(self, version: str) -> list[tuple[str, str | None]]:
        """Return note filename and revision pairs for a version."""

    def parse_note_file(self, filename: str, sha: str | None) -> dict[str, Any]:
        """Parse one note file."""


@dataclass
class RenderedNotes:
    """Prelude paragraphs and section items gathered from note files."""

    prelude: list[str] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)


def _normalize_version(version: str) -> str:
    """Normalize a release version by removing its optional leading ``v``."""
    return version[1:] if version.startswith("v") else version


def select_versions(
    versions: Sequence[str], tags: set[str], version: str | None
) -> list[str]:
    """Select notes for a release tag or all versions since the latest tag."""
    normalized_tags = {_normalize_version(tag) for tag in tags}
    if version is not None:
        normalized_version = _normalize_version(version)
        return [
            candidate
            for candidate in versions
            if _normalize_version(candidate) == normalized_version
        ]
    selected = []
    for candidate in versions:
        if _normalize_version(candidate) in normalized_tags:
            break
        selected.append(candidate)
    return selected


def collect_notes(
    loader: NoteLoader,
    versions: Sequence[str],
    section_names: Sequence[str],
    prelude_name: str,
) -> RenderedNotes:
    """Gather note text and reject malformed section values."""
    notes = RenderedNotes()
    for version in versions:
        for filename, sha in loader[version]:
            content = loader.parse_note_file(filename, sha)
            if not isinstance(content, dict):
                raise ValueError(f"{filename}: note contents must be a mapping")
            allowed_sections = set(section_names) | {prelude_name}
            unexpected = content.keys() - allowed_sections
            if unexpected:
                names = ", ".join(sorted(repr(name) for name in unexpected))
                raise ValueError(
                    f"{filename}: unknown release note section(s): {names}"
                )
            if prelude_name in content:
                prelude = content[prelude_name]
                if not isinstance(prelude, str):
                    raise ValueError(f"{filename}: {prelude_name} must be a string")
                if prelude.strip():
                    notes.prelude.append(prelude)
            for name in section_names:
                if name not in content:
                    continue
                items = content[name]
                if isinstance(items, str):
                    items = [items]
                if not isinstance(items, list) or not all(
                    isinstance(item, str) for item in items
                ):
                    raise ValueError(f"{filename}: {name} must be a string or list")
                notes.sections.setdefault(name, []).extend(
                    item for item in items if item.strip()
                )
    return notes


def validate_upgrade_notes(paths: Sequence[Path]) -> None:
    """Require each breaking-change note to contain operator guidance."""
    if not paths:
        raise ValueError("breaking-change pull requests must add a release note")
    for path in paths:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            raise ValueError(f"{path}: note contents must be a mapping")
        upgrade = content.get("upgrade")
        if isinstance(upgrade, str):
            upgrade = [upgrade]
        if not isinstance(upgrade, list) or not all(
            isinstance(item, str) for item in upgrade
        ):
            raise ValueError(f"{path}: breaking-change note needs an upgrade section")
        placeholders = {
            "todo",
            "tbd",
            "n/a",
            "describe the required operator action and its impact.",
        }
        useful_items = [
            item.strip()
            for item in upgrade
            if item.strip() and item.strip().casefold() not in placeholders
        ]
        if not useful_items:
            raise ValueError(f"{path}: upgrade section needs operator guidance")


def added_release_note_paths(
    repo_root: Path, base_sha: str, head_sha: str
) -> list[Path]:
    """Return added note paths from a pull request using NUL-safe Git output."""
    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=A",
            "-z",
            base_sha,
            head_sha,
            "--",
            f"{RELNOTES_DIR}/notes/*.yaml",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    return [Path(os.fsdecode(path)) for path in result.stdout.split(b"\0") if path]


def format_item(text: str) -> str:
    """Render a note item as a Markdown bullet with indented continuation."""
    lines = textwrap.dedent(text).strip().splitlines()
    rest = [f"  {line}" if line.strip() else "" for line in lines[1:]]
    return "\n".join([f"- {lines[0]}", *rest])


def render_markdown(
    notes: RenderedNotes, sections: Sequence[tuple[str, str, int]]
) -> str:
    """Render collected release notes as Markdown."""
    blocks = [textwrap.dedent(text).strip() for text in notes.prelude]
    for name, title, level in sections:
        items = notes.sections.get(name)
        if not items:
            continue
        blocks.append(f"{'#' * (2 + level)} {title}")
        blocks.append("\n".join(format_item(item) for item in items))
    return "\n\n".join(blocks)


def _literal_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    """Dump multi-line values as readable YAML literal blocks."""
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


class _ConfigDumper(yaml.SafeDumper):
    """YAML dumper that keeps multi-line strings readable."""


_ConfigDumper.add_representer(str, _literal_str)


def build_drafter_config(base_config: str, notes_markdown: str) -> str:
    """Add rendered notes to the Release Drafter header."""
    if not notes_markdown:
        return base_config
    config = yaml.safe_load(base_config)
    if not isinstance(config, dict):
        raise ValueError("Release Drafter config must be a mapping")
    header = config.get("header", "")
    if not isinstance(header, str):
        raise ValueError("Release Drafter header must be a string")
    if header and not header.endswith("\n\n"):
        header = header.rstrip("\n") + "\n\n"
    config["header"] = f"{header}{notes_markdown.rstrip()}\n\n"
    dumped: str = yaml.dump(
        config,
        Dumper=_ConfigDumper,
        sort_keys=False,
        allow_unicode=True,
        width=1_000_000,
    )
    header = "---\n# Generated by scripts/render_release_notes.py; do not edit.\n"
    return header + dumped


def git_tags(repo_root: Path) -> set[str]:
    """Return all tag names from the repository."""
    result = subprocess.run(
        ["git", "tag", "--list"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(result.stdout.split())


def load_release_notes(repo_root: Path, version: str | None) -> str:
    """Scan repository notes and render only the selected release versions."""
    from reno import config as reno_config
    from reno import loader as reno_loader

    conf = reno_config.Config(str(repo_root), RELNOTES_DIR)
    sections = [(s.name, s.title, s.section_level) for s in conf.sections]
    tags = git_tags(repo_root)
    with reno_loader.Loader(conf) as loader:
        selected = select_versions(loader.versions, tags, version)
        notes = collect_notes(
            loader,
            selected,
            [section[0] for section in sections],
            conf.prelude_section_name,
        )
    return render_markdown(notes, sections)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="repository to scan (default: current directory)",
    )
    parser.add_argument(
        "--version",
        help="render notes for an existing release tag instead of unreleased notes",
    )
    parser.add_argument(
        "--release-drafter-config",
        type=Path,
        help="write a Release Drafter config with rendered notes in its header",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write output to a file instead of standard output",
    )
    parser.add_argument(
        "--require-upgrade",
        type=Path,
        nargs="+",
        help="validate that added breaking-change notes contain upgrade guidance",
    )
    parser.add_argument(
        "--require-upgrade-pr",
        action="store_true",
        help="discover added note files in a pull request and validate guidance",
    )
    parser.add_argument("--base-sha", help=argparse.SUPPRESS)
    parser.add_argument("--head-sha", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the renderer or upgrade-note validator."""
    args = parse_args(argv)
    try:
        if args.require_upgrade_pr:
            if args.base_sha is None or args.head_sha is None:
                raise ValueError("PR note validation requires base and head SHAs")
            added = added_release_note_paths(
                args.repo_root, args.base_sha, args.head_sha
            )
            validate_upgrade_notes([args.repo_root / path for path in added])
            return 0
        if args.require_upgrade is not None:
            validate_upgrade_notes(args.require_upgrade)
            return 0
        tags = git_tags(args.repo_root)
        if args.version is not None and _normalize_version(args.version) not in {
            _normalize_version(tag) for tag in tags
        }:
            raise ValueError(f"{args.version!r} is not a git tag")
        output = load_release_notes(args.repo_root, args.version)
        if args.release_drafter_config is not None:
            output = build_drafter_config(
                args.release_drafter_config.read_text(encoding="utf-8"), output
            )
        elif output:
            output += "\n"
        if args.output is not None:
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
    except (OSError, ValueError, yaml.YAMLError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
