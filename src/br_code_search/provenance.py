"""Read-only Git provenance helpers for reference source roots.

The reference corpus may be a plain directory today and a Git checkout later.
This module never stages, commits, fetches, or modifies a repository; it only
reports the revision that an index was built from when Git metadata exists.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


def _run_git(root: Path, *arguments: str) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def inspect_git(root: str | os.PathLike[str]) -> dict[str, Any]:
    """Inspect one source root's Git revision without changing repository state."""
    resolved = Path(root).expanduser().resolve()
    if not resolved.is_dir():
        return {
            "ok": False,
            "available": False,
            "root": str(resolved),
            "error": f"Source root does not exist or is not a directory: {resolved}",
        }
    code, is_repo, error = _run_git(resolved, "rev-parse", "--is-inside-work-tree")
    if code == 127:
        return {"ok": False, "available": False, "root": str(resolved), "error": f"Git is unavailable: {error}"}
    if code != 0 or is_repo.casefold() != "true":
        return {
            "ok": True,
            "available": False,
            "root": str(resolved),
            "revision": None,
            "branch": None,
            "dirty": None,
            "remotes": [],
            "warnings": ["Source root is not a Git work tree; index provenance is path/time based only."],
        }
    _, top_level, _ = _run_git(resolved, "rev-parse", "--show-toplevel")
    _, revision, revision_error = _run_git(resolved, "rev-parse", "HEAD")
    _, branch, _ = _run_git(resolved, "branch", "--show-current")
    _, commit_time, _ = _run_git(resolved, "show", "-s", "--format=%cI", "HEAD")
    _, status_text, _ = _run_git(resolved, "status", "--porcelain", "--untracked-files=no")
    _, remote_text, _ = _run_git(resolved, "remote")
    warnings: list[str] = []
    if not revision:
        warnings.append(revision_error or "Git work tree has no HEAD revision yet.")
    if status_text:
        warnings.append("Source work tree has tracked changes not represented by a clean revision.")
    return {
        "ok": True,
        "available": True,
        "root": top_level or str(resolved),
        "revision": revision or None,
        "branch": branch or None,
        "commit_time": commit_time or None,
        "dirty": bool(status_text),
        "remotes": [line for line in remote_text.splitlines() if line.strip()],
        "warnings": warnings,
    }

