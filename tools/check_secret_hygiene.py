#!/usr/bin/env python3
"""Fail closed on committed credential files and high-confidence plaintext keys.

Findings report only a path and line number; the suspected value is never echoed.
This complements provider-specific runtime tests and hosted repository scanning.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import sys


FORBIDDEN_PARTS = {".secrets", "secrets"}
FORBIDDEN_NAMES = {
    ".env",
    "acquisition-services.json",
    "metadata-providers.json",
}
HIGH_CONFIDENCE = (
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    # A tailnet address (the 100.64/10 range) names a machine on a private
    # network. A tunnel command in a README published one; write a
    # placeholder such as <backend-address> instead.
    re.compile(rb"(?<![\d.])100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}(?![\d.])"),
)
GENERIC_ASSIGNMENT = re.compile(
    rb"(?i)(?:api[_-]?key|apikey|access[_-]?token|authorization|bearer[_-]?token)"
    rb"\s*[=:]\s*['\"]([^'\"\r\n]{16,})['\"]"
)
PLACEHOLDER_WORDS = (
    b"test",
    b"secret",
    b"opaque",
    b"example",
    b"redacted",
    b"placeholder",
    b"must-not",
    b"your-",
    b"<",
)

# Release verification also runs from an exported, read-only source bundle inside
# the minimal application image. That image intentionally contains neither Git
# nor development-only trees, so retain a bounded filesystem fallback instead of
# adding Git to the production image solely for this check.
FALLBACK_EXCLUDED_DIRECTORIES = {
    ".checkpoint-output",
    ".git",
    ".mypy_cache",
    ".pnpm-store",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "data",
    "node_modules",
}


def candidate_paths(root: Path) -> list[Path]:
    if shutil.which("git") is not None and (root / ".git").exists():
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            cwd=root,
            check=True,
            capture_output=True,
        )
        return [root / item.decode() for item in result.stdout.split(b"\0") if item]

    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and not any(
            part in FALLBACK_EXCLUDED_DIRECTORIES
            for part in path.relative_to(root).parts[:-1]
        )
    )


def findings(root: Path) -> list[tuple[Path, int, str]]:
    found: list[tuple[Path, int, str]] = []
    for path in candidate_paths(root):
        relative = path.relative_to(root)
        lowered_parts = {part.casefold() for part in relative.parts[:-1]}
        name = relative.name.casefold()
        if (
            lowered_parts & FORBIDDEN_PARTS
            or name in FORBIDDEN_NAMES
            or name.endswith(".credentials.json")
            or name.endswith(".secret")
        ):
            found.append((relative, 0, "credential file is tracked"))
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if b"\0" in content:
            continue
        for line_number, line in enumerate(content.splitlines(), 1):
            if any(pattern.search(line) for pattern in HIGH_CONFIDENCE):
                found.append((relative, line_number, "high-confidence secret pattern"))
                continue
            match = GENERIC_ASSIGNMENT.search(line)
            if match and not any(
                word in match.group(1).lower() for word in PLACEHOLDER_WORDS
            ):
                found.append((relative, line_number, "literal credential assignment"))
    return found


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    found = findings(root)
    for path, line, reason in found:
        location = f"{path}:{line}" if line else str(path)
        print(f"{location}: {reason}")
    if found:
        print(f"Secret hygiene failed with {len(found)} redacted finding(s).")
        return 1
    print("Secret hygiene passed; no credential files or plaintext key patterns found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
