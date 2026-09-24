#!/usr/bin/env python3
"""Publish the canonical classifier-skill repository to The Build Loop.

The downstream repository is a generated mirror, not a second authoring source. Its main branch
always carries the canonical tree; when its history has diverged, a commit with that tree is added
on top of it, so downstream history is preserved.
Usage: sync_the_build_loop.py [--check] [--remote URL] [--source-ref REF]"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE = "https://github.com/The-Build-Loop/classifier-skill.git"


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=check, capture_output=True, text=True)


def validate(source_ref: str) -> str:
    if not (ROOT / "SKILL.md").exists():
        raise SystemExit("Refusing to publish: classifier-skill checkout not found.")
    if git("rev-parse", "--verify", f"{source_ref}^{{commit}}", check=False).returncode:
        raise SystemExit(f"Refusing to publish: {source_ref} is not a commit.")
    if source_ref == "HEAD" and git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise SystemExit("Refusing to publish uncommitted classifier-skill changes; commit them first.")
    return git("rev-parse", f"{source_ref}^{{commit}}").stdout.strip()


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
    parser.add_argument("--source-ref", default="HEAD", help="canonical commit to publish (default: HEAD)")
    args = parser.parse_args()

    source_commit = validate(args.source_ref)
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
