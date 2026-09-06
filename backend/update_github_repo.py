"""
Incremental GitHub sync for locally-stored codebase JSON files.

Refresh a repo's local codebase index (full + lightweight) using a Git commit
SHA diff instead of re-fetching every file from scratch.

How it works:
  - The full JSON (e.g. codebase_paytm.json) now stores the SHA of the last
    synced commit under the "last_commit_sha" key (populated by fetchGithub).
  - On update we fetch the repo's latest commit SHA via GitHub's compare API.
  - If the stored SHA matches the latest SHA, nothing changed -> no-op.
  - Otherwise we diff the two SHAs and only re-fetch the files that were
    added, modified, or renamed (and drop removed files). This keeps the local
    index in sync without refetching the whole repo.

Usage:
    python updateGithubRepo.py                     # update the default repo
    python updateGithubRepo.py --repo-url <url>    # update a specific repo
    python updateGithubRepo.py --full-json <path>  # explicit full JSON path
    python updateGithubRepo.py --check             # just report if outdated

If the full JSON has no "last_commit_sha" yet (first run on an old index), this
script does NOT re-fetch the whole repo. It assumes the existing local index is
the current baseline and only stamps the latest commit SHA onto it (plus a cheap
metadata refresh), so every subsequent run is a true incremental sync that
fetches only the files that actually changed.
"""

import argparse
import base64
import json
import os
import sys

from github import Auth, Github
from dotenv import load_dotenv

from fetchGithub import (
    build_lightweight_context,
    extract_imports_js,
    extract_imports_python,
    get_file_language,
    should_skip,
)

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Defaults matching the existing local indices (override via argv).
DEFAULT_REPO_URL = "https://github.com/aadityasingh9601/Jarvis"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FULL_JSON = os.path.join(SCRIPT_DIR, "codebase_jarvis.json")
DEFAULT_LIGHT_JSON = os.path.join(SCRIPT_DIR, "codebase_jarvis_lightweight.json")


def _make_repo(repo_url: str):
    auth = Auth.Token(GITHUB_TOKEN)
    g = Github(auth=auth)
    parts = repo_url.replace("https://github.com/", "").rstrip("/").split("/")
    owner, repo_name = parts[0], parts[1]
    return g.get_repo(f"{owner}/{repo_name}")


def _decode_contents(repo, path: str) -> str:
    """Return the decoded text content of a file at the given repo path."""
    file_content = repo.get_contents(path)
    if isinstance(file_content, list):  # path might be a directory; unexpected
        return None
    return base64.b64decode(file_content.content).decode("utf-8")


def _build_file_entry(repo, path: str) -> dict:
    """Build a full file entry (language, size_lines, imports, content)."""
    decoded = _decode_contents(repo, path)
    if decoded is None:
        return None
    language = get_file_language(path)
    if language == 'python':
        imports = extract_imports_python(decoded)
    elif language in ['javascript', 'typescript', 'tsx', 'jsx']:
        imports = extract_imports_js(decoded)
    else:
        imports = []
    return {
        "language": language,
        "size_lines": len(decoded.splitlines()),
        "imports": imports,
        "content": decoded,
    }


def sync_repo(
    repo_url: str = DEFAULT_REPO_URL,
    full_json: str = DEFAULT_FULL_JSON,
    light_json: str = DEFAULT_LIGHT_JSON,
) -> dict:
    """Incrementally update the local index for a repo.

    Returns a dict summarizing what happened: {"changed": bool, "added": [...],
    "modified": [...], "removed": [...]}.
    """
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN not set in environment.")

    repo = _make_repo(repo_url)

    if not os.path.exists(full_json):
        raise FileNotFoundError(f"Full JSON not found: {full_json}")

    with open(full_json, "r") as f:
        existing = json.load(f)

    old_sha = existing.get("last_commit_sha")
    default_branch = existing.get("default_branch") or repo.default_branch
    latest_sha = repo.get_branch(default_branch).commit.sha

    summary = {"added": [], "modified": [], "removed": [], "renamed": [], "changed": False}

    # No baseline SHA stored yet (e.g. an index built by an older fetchGithub
    # that predates last_commit_sha). We can't diff against history without a
    # baseline, so rather than re-downloading the whole repo we treat the
    # existing local index as current and simply stamp the repo's latest SHA as
    # the baseline (plus a cheap metadata refresh). Every subsequent run then
    # does a true incremental diff and only fetches changed files.
    if not old_sha:
        existing["description"] = repo.description or existing.get("description", "")
        existing["default_branch"] = repo.default_branch
        existing["last_commit_sha"] = latest_sha
        _persist(existing, build_lightweight_context(existing), full_json, light_json)
        print(
            f"No previous commit SHA. Stamped baseline at {latest_sha} "
            "without re-fetching the repo. Next runs will be incremental."
        )
        summary["changed"] = True
        return summary

    if old_sha == latest_sha:
        print(f"Repo already up to date at commit {latest_sha}. Nothing to do.")
        return summary

    print(f"Detected change: {old_sha} -> {latest_sha}")
    compare = repo.compare(old_sha, latest_sha)

    for file_diff in compare.files:
        path = file_diff.filename
        status = file_diff.status  # added | modified | removed | renamed

        if should_skip(path):
            print(f"  skipped (filtered): {path}")
            continue

        if status == "removed":
            existing["files"].pop(path, None)
            if path in existing["file_tree"]:
                existing["file_tree"].remove(path)
            summary["removed"].append(path)
            print(f"  removed: {path}")
            continue

        if status == "renamed":
            # Handle the old name removal + new name addition.
            old_path = getattr(file_diff, "previous_filename", None) or path
            if old_path in existing["files"]:
                del existing["files"][old_path]
                if old_path in existing["file_tree"]:
                    existing["file_tree"].remove(old_path)
                summary["renamed"].append(f"{old_path} -> {path}")

        try:
            entry = _build_file_entry(repo, path)
            if entry is None:
                print(f"  could not read entry (directory?): {path}")
                continue
            existing["files"][path] = entry
            if path not in existing["file_tree"]:
                existing["file_tree"].append(path)
            key = "modified" if status != "added" else "added"
            summary[key].append(path)
            print(f"  {summary[key]}: {path}")
        except Exception as e:
            print(f"  ERROR reading {path}: {e}")

    # Refresh repo metadata (description may have changed) and the SHA.
    existing["description"] = repo.description or existing.get("description", "")
    existing["default_branch"] = repo.default_branch
    existing["last_commit_sha"] = latest_sha

    summary["changed"] = bool(summary["added"] or summary["modified"] or summary["removed"] or summary["renamed"])

    _persist(existing, build_lightweight_context(existing), full_json, light_json)
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
    repo = _make_repo(repo_url)
    if not os.path.exists(full_json):
        return True
    with open(full_json, "r") as f:
        existing = json.load(f)
    old_sha = existing.get("last_commit_sha")
    branch = existing.get("default_branch") or repo.default_branch
    return old_sha != repo.get_branch(branch).commit.sha


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
        return

    sync_repo(args.repo_url, args.full_json, args.light_json)


if __name__ == "__main__":
    sys.exit(main())
