#!/usr/bin/env python3
"""Classify a Dependabot commit message as major / non-major / unknown.

Used by .github/workflows/dependabot-auto-merge.yml. Reads the commit message
on stdin and prints exactly one word:

  non-major  every updated dependency is a minor or patch bump -> safe to merge
  major      at least one dependency is a semver-major bump    -> manual review
  unknown    the bump size could not be determined reliably    -> manual review

For each dependency in the ``updated-dependencies`` metadata block:

1. If Dependabot recorded ``update-type: version-update:semver-*``, that wins.
2. Otherwise (e.g. "Update X requirement from >=3.3.6 to >=3.3.8", where
   Dependabot omits update-type) the old -> new versions are read from the
   message itself (subject line, "Updates `X` from A to B", "Bumps [X](..) from
   A to B", or a "| [X](..) | `A` | `B` |" table row) and compared. The new
   version must match the metadata's ``dependency-version``.

A version range that is not a single plain version (``>=1.2,<2``, ``1.0.0rc1``)
is ``unknown``. A 0.x minor change (0.4 -> 0.5) counts as major, since 0.x
minors may break. Anything unparseable is ``unknown``; never guess.

Stdlib only: the workflow runs it with the runner's system python3.
"""

from __future__ import annotations

import re
import sys

MAJOR = "major"
NON_MAJOR = "non-major"
UNKNOWN = "unknown"

_SUBJECT_RE = re.compile(
    r"^(?:Bump|Update) (?P<name>\S+?)(?: requirement)? from (?P<old>\S+) to (?P<new>\S+?)"
    r"(?: in \S+)?(?: \(#\d+\))?\s*$"
)
_UPDATES_RE = re.compile(
    r"^Updates `(?P<name>[^`]+)` from (?P<old>\S+) to (?P<new>\S+?)\.?\s*$"
)
_BUMPS_RE = re.compile(
    r"^Bumps \[(?P<name>[^\]]+)\]\([^)]*\) from (?P<old>\S+) to (?P<new>\S+?)\.?\s*$"
)
_TABLE_RE = re.compile(
    r"^\|\s*\[(?P<name>[^\]]+)\]\([^)]*\)\s*\|\s*`(?P<old>[^`]+)`\s*\|\s*`(?P<new>[^`]+)`\s*\|\s*$"
)
_VERSION_RE = re.compile(r"^(?:>=|==|~=|\^|~|=)?v?(\d+(?:\.\d+)*)$")


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def parse_updated_dependencies(message: str) -> list[dict[str, str]]:
    """Return the entries of the ``updated-dependencies`` block (empty if absent)."""
    entries: list[dict[str, str]] = []
    in_block = False
    for raw in message.splitlines():
        line = raw.rstrip()
        if not in_block:
            if line.strip() == "updated-dependencies:":
                in_block = True
            continue
        if line.strip() == "...":
            break
        stripped = line.strip()
        if stripped.startswith("- "):
            entries.append({})
            stripped = stripped[2:]
        if not entries or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        entries[-1][key.strip()] = _strip_quotes(value)
    return [entry for entry in entries if entry.get("dependency-name")]


def parse_version_changes(message: str) -> dict[str, tuple[str, str]]:
    """Map dependency name -> (old, new) as written in the message text."""
    changes: dict[str, tuple[str, str]] = {}
    lines = message.splitlines()
    for index, raw in enumerate(lines):
        line = raw.strip()
        patterns = (_UPDATES_RE, _BUMPS_RE, _TABLE_RE)
        if index == 0:
            patterns = (_SUBJECT_RE, *patterns)
        for pattern in patterns:
            match = pattern.match(line)
            if match:
                changes.setdefault(match["name"], (match["old"], match["new"]))
                break
    return changes


def parse_version(text: str) -> tuple[int, ...] | None:
    match = _VERSION_RE.match(text.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _pad(version: tuple[int, ...], size: int) -> tuple[int, ...]:
    return version + (0,) * (size - len(version))


def classify_change(old_text: str, new_text: str) -> str:
    old, new = parse_version(old_text), parse_version(new_text)
    if old is None or new is None:
        return UNKNOWN
    size = max(len(old), len(new), 3)
    old, new = _pad(old, size), _pad(new, size)
    if new <= old:
        return UNKNOWN
    if old[0] != new[0]:
        return MAJOR
    if old[0] == 0 and old[1] != new[1]:
        return MAJOR
    return NON_MAJOR


def classify(message: str) -> str:
    entries = parse_updated_dependencies(message)
    if not entries:
        return UNKNOWN
    changes = parse_version_changes(message)
    verdicts = []
    for entry in entries:
        update_type = entry.get("update-type", "")
        if update_type == "version-update:semver-major":
            verdicts.append(MAJOR)
            continue
        if update_type in (
            "version-update:semver-minor",
            "version-update:semver-patch",
        ):
            verdicts.append(NON_MAJOR)
            continue
        if update_type:
            verdicts.append(UNKNOWN)
            continue
        change = changes.get(entry["dependency-name"])
        if change is None:
            verdicts.append(UNKNOWN)
            continue
        old_text, new_text = change
        recorded = entry.get("dependency-version")
        if recorded is not None:
            new, want = parse_version(new_text), parse_version(recorded)
            if new is None or want is None:
                verdicts.append(UNKNOWN)
                continue
            size = max(len(new), len(want))
            if _pad(new, size) != _pad(want, size):
                verdicts.append(UNKNOWN)
                continue
        verdicts.append(classify_change(old_text, new_text))
    if MAJOR in verdicts:
        return MAJOR
    if UNKNOWN in verdicts:
        return UNKNOWN
    return NON_MAJOR


def main() -> int:
    print(classify(sys.stdin.read()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
