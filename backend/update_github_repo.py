"""
Incremental GitHub sync for locally-stored codebase JSON files.

Refresh a repo's local codebase index (full + lightweight) using a commit SHA
comparison, backed by a fast shallow clone (`git clone --depth 1`) instead of
the old PyGithub/API flow that downloaded every file one-by-one.

How it works:
  - The full JSON (e.g. codebase_jarvis.json) stores the SHA of the last
    synced commit under the "last_commit_sha" key (populated by fetch_github).
  - On update we fetch the repo's latest commit SHA via `git ls-remote` (a
    single cheap network call) and/or a fresh shallow clone.
  - If the stored SHA matches the latest SHA, nothing changed -> no-op.
  - Otherwise we shallow-clone the latest commit, rebuild the index from the
    local checkout, and compute added/modified/removed/renamed by comparing it
    against the existing JSON. The full + lightweight JSON files are then
    persisted in the exact same format as before.

Usage:
    python update_github_repo.py                     # update the default repo
    python update_github_repo.py --repo-url <url>    # update a specific repo
    python update_github_repo.py --full-json <path>  # explicit full JSON path
    python update_github_repo.py --check             # just report if outdated
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv

from fetch_github import (
    build_lightweight_context,
    fetch_repo_as_structured_json,
    get_repo_commit_sha,
)

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") or ""

# Defaults matching the existing local indices (override via argv).
DEFAULT_REPO_URL = "https://github.com/aadityasingh9601/Jarvis"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FULL_JSON = os.path.join(SCRIPT_DIR, "codebase_jarvis.json")
DEFAULT_LIGHT_JSON = os.path.join(SCRIPT_DIR, "codebase_jarvis_lightweight.json")


def _compute_diff(old_files: dict, new_files: dict) -> tuple:
    """Compare two `files` dicts and return (added, removed, modified, renamed).

    A removed file whose content matches an added file's content is reported as
    a rename instead of an add + remove.
    """
    old_set, new_set = set(old_files), set(new_files)
    added = sorted(new_set - old_set)
    removed = sorted(old_set - new_set)
    modified = sorted(
        p for p in old_set & new_set
        if old_files[p].get("content") != new_files[p].get("content")
    )

    renamed = []
    added_by_content = {}
    for p in added:
        added_by_content.setdefault(new_files[p].get("content"), []).append(p)

    kept_removed = []
    for p in removed:
        matches = added_by_content.get(old_files[p].get("content"))
        if matches:
            new_path = matches.pop(0)
            renamed.append(f"{p} -> {new_path}")
            added.remove(new_path)
        else:
            kept_removed.append(p)

    return added, kept_removed, modified, renamed


def sync_repo(
    repo_url: str = DEFAULT_REPO_URL,
    full_json: str = DEFAULT_FULL_JSON,
    light_json: str = DEFAULT_LIGHT_JSON,
) -> dict:
    """Update the local index for a repo.

    Returns a dict summarizing what happened: {"changed": bool, "added": [...],
    "modified": [...], "removed": [...], "renamed": [...]}.
    """
    if not os.path.exists(full_json):
        raise FileNotFoundError(f"Full JSON not found: {full_json}")

    with open(full_json, "r") as f:
        existing = json.load(f)

    old_sha = existing.get("last_commit_sha")

    summary = {"added": [], "modified": [], "removed": [], "renamed": [], "changed": False}

    # No baseline SHA stored yet (e.g. an index built by an older fetcher that
    # predates last_commit_sha). Rebuild the baseline from a fresh shallow clone
    # so every subsequent run is a true incremental SHA diff.
    if not old_sha:
        fresh = fetch_repo_as_structured_json(repo_url, GITHUB_TOKEN)
        _persist(fresh, build_lightweight_context(fresh), full_json, light_json)
        print(
            f"No previous commit SHA. Stamped baseline at {fresh['last_commit_sha']} "
            "from a shallow clone. Next runs will be incremental."
        )
        summary["changed"] = True
        return summary

    # Fast pre-check: resolve the remote HEAD SHA without downloading anything.
    latest_sha = get_repo_commit_sha(repo_url, GITHUB_TOKEN)

    if old_sha == latest_sha:
        print(f"Repo already up to date at commit {latest_sha}. Nothing to do.")
        return summary

    print(f"Detected change: {old_sha} -> {latest_sha}")

    # Latest commit differs, so grab it (shallow clone) and diff against the
    # existing local index.
    fresh = fetch_repo_as_structured_json(repo_url, GITHUB_TOKEN)
    latest_sha = fresh["last_commit_sha"]

    added, removed, modified, renamed = _compute_diff(existing.get("files", {}), fresh["files"])

    for path in added:
        print(f"  added: {path}")
    for path in modified:
        print(f"  modified: {path}")
    for path in removed:
        print(f"  removed: {path}")
    for path in renamed:
        print(f"  renamed: {path}")

    # Rebuild the index from the freshly cloned checkout, keeping the local
    # description if the metadata refresh came back empty.
    updated = {
        "repo_name": fresh["repo_name"],
        "description": fresh["description"] or existing.get("description", ""),
        "default_branch": fresh["default_branch"],
        "last_commit_sha": latest_sha,
        "file_tree": fresh["file_tree"],
        "files": fresh["files"],
    }

    summary["added"] = added
    summary["modified"] = modified
    summary["removed"] = removed
    summary["renamed"] = renamed
    summary["changed"] = bool(added or modified or removed or renamed)

    _persist(updated, build_lightweight_context(updated), full_json, light_json)
    print(f"Index updated to commit {latest_sha}.")
    return summary


def _persist(full: dict, lightweight: dict, full_json: str, light_json: str) -> None:
    with open(full_json, "w") as f:
        json.dump(full, f, indent=2)
    with open(light_json, "w") as f:
        json.dump(lightweight, f, indent=2)
    print(f"Saved full index -> {full_json}")
    print(f"Saved lightweight index -> {light_json}")


def latest_commit_outdated(repo_url: str = DEFAULT_REPO_URL, full_json: str = DEFAULT_FULL_JSON) -> bool:
    """Return True if the local index is behind the repo's latest commit."""
    if not os.path.exists(full_json):
        return True
    with open(full_json, "r") as f:
        existing = json.load(f)
    old_sha = existing.get("last_commit_sha")
    return old_sha != get_repo_commit_sha(repo_url, GITHUB_TOKEN)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL, help="GitHub repo URL")
    parser.add_argument("--full-json", default=DEFAULT_FULL_JSON, help="Path to full codebase JSON")
    parser.add_argument("--light-json", default=DEFAULT_LIGHT_JSON, help="Path to lightweight codebase JSON")
    parser.add_argument("--check", action="store_true", help="Only report whether the index is outdated")
    args = parser.parse_args()

    if args.check:
        outdated = latest_commit_outdated(args.repo_url, args.full_json)
        print("OUTDATED" if outdated else "UP_TO_DATE")
        return 0 if not outdated else 1

    sync_repo(args.repo_url, args.full_json, args.light_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())