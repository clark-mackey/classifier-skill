#!/usr/bin/env python3
"""Publish the canonical classifier-skill repository to The Build Loop.

The downstream repository is a generated mirror of what is pushed to the canonical GitHub
repository's main branch, never of local commits or files. Its main branch always carries that
tree; when its history has diverged, a commit with that tree is added on top of it, so downstream
history is preserved.
Usage: sync_the_build_loop.py [--check] [--remote URL] [--canonical URL] [--source-ref REF]"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE = "https://github.com/The-Build-Loop/classifier-skill.git"
CANONICAL_REMOTE = "https://github.com/clark-mackey/classifier-skill.git"


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=check, capture_output=True, text=True)


def canonical_commit(canonical: str, source_ref: Optional[str]) -> str:
    """The commit to publish: canonical main as pushed, or an older commit already on it."""
    if not (ROOT / "SKILL.md").exists():
        raise SystemExit("Refusing to publish: classifier-skill checkout not found.")
    fetched = git("fetch", "--quiet", canonical, "main", check=False)
    if fetched.returncode:
        raise SystemExit(f"Cannot fetch canonical main from {canonical}: {fetched.stderr.strip()}")
    pushed = git("rev-parse", "FETCH_HEAD^{commit}").stdout.strip()
    if source_ref is None:
        return pushed
    resolved = git("rev-parse", "--verify", f"{source_ref}^{{commit}}", check=False)
    if resolved.returncode:
        raise SystemExit(f"Refusing to publish: {source_ref} is not a commit.")
    commit = resolved.stdout.strip()
    if git("merge-base", "--is-ancestor", commit, pushed, check=False).returncode:
        raise SystemExit(f"Refusing to publish {source_ref}: it is not on canonical main; push it first.")
    return commit


def remote_state(remote: str) -> tuple[Optional[str], Optional[str]]:
    listing = git("ls-remote", "--heads", remote, "main", check=False)
    if listing.returncode:
        raise SystemExit(f"Cannot reach {remote}: {listing.stderr.strip()}")
    if not listing.stdout.strip():
        return None, None
    git("fetch", "--quiet", remote, "main")
    return (git("rev-parse", "FETCH_HEAD^{commit}").stdout.strip(),
            git("rev-parse", "FETCH_HEAD^{tree}").stdout.strip())


def publication_commit(source_commit: str, source_tree: str, downstream_commit: Optional[str]) -> str:
    if downstream_commit is None:
        return source_commit
    if git("merge-base", "--is-ancestor", downstream_commit, source_commit, check=False).returncode == 0:
        return source_commit
    subject = git("show", "-s", "--format=%s", source_commit).stdout.strip()
    message = f"{subject}\n\nCanonical-Commit: {source_commit}"
    return git("commit-tree", source_tree, "-p", downstream_commit, "-m", message).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish or check The-Build-Loop/classifier-skill.")
    parser.add_argument("--check", action="store_true", help="report drift without pushing")
    parser.add_argument("--remote", default=DEFAULT_REMOTE, help=f"downstream Git remote (default: {DEFAULT_REMOTE})")
    parser.add_argument("--canonical", default=CANONICAL_REMOTE, help=f"canonical Git remote (default: {CANONICAL_REMOTE})")
    parser.add_argument("--source-ref", help="an older commit on canonical main to publish (default: canonical main)")
    args = parser.parse_args()

    source_commit = canonical_commit(args.canonical, args.source_ref)
    source_tree = git("rev-parse", f"{source_commit}^{{tree}}").stdout.strip()
    downstream_commit, downstream_tree = remote_state(args.remote)

    if downstream_tree == source_tree:
        print("The Build Loop classifier-skill repository matches canonical.")
        return 0
    if args.check:
        print("The Build Loop classifier-skill repository has drifted.", file=sys.stderr)
        return 1
    publish = publication_commit(source_commit, source_tree, downstream_commit)
    git("push", args.remote, f"{publish}:refs/heads/main")
    print(f"Published classifier-skill {source_commit[:7]} to {args.remote}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
