"""
update_github_repo.py — incrementally refresh a locally-stored repo context.

Takes a repo context that is ALREADY materialized on local disk (the JSON
indices in ./codebases and the session .dill in ./rlm_sessions) and brings it
up to date with the repo's newest commit:

  1. Reads the stored "last_commit_sha" from the local index.
  2. Resolves the remote HEAD via `git ls-remote` (one cheap network call).
  3. If the SHA is unchanged -> no-op, nothing is written or uploaded.
  4. Otherwise shallow-clones the repo, fetches the old commit so the two can
     be diffed, and asks git which paths actually changed between the two
     commits. Only those files are re-read and re-parsed; every unchanged file
     reuses the content already in the index.
  5. Rewrites both JSON indices with the new last_commit_sha.
  6. Uploads them to Supabase under the NEW commit's folder.
  7. Refreshes the session .dill so its in-REPL context matches the refreshed
     index, and uploads that under the new commit too.

The commit SHA is part of every remote path, so contexts for the old commit
stay intact and untouched for any user still working on it.

This only makes sense WHILE a user's session is active, i.e. when the repo
context is already present locally. It refuses to run otherwise, because
without a local index there is nothing to update incrementally.

Usage:
    python update_github_repo.py --repo owner/repo --user <user_id>
    python update_github_repo.py --repo owner/repo --user <user_id> --check
    python update_github_repo.py --repo owner/repo --user <user_id> \\
        --full-json codebases/codebase_foo.json --light-json codebases/codebase_foo_lightweight.json

Defaults are resolved from the local index, so the common case needs no paths:
    python update_github_repo.py --user user123
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

from dotenv import load_dotenv

from build_repl_state import build_repl_state_from_json, upload_dill_to_supabase
from fetch_github_repo import clone_repo
from parse_github_repo import _parse_file, parse_repo, should_skip, upload_to_supabase

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CODEBASES_DIR = os.path.join(SCRIPT_DIR, "codebases")
DEFAULT_STORAGE_PATH = os.path.join(SCRIPT_DIR, "rlm_sessions")
DEFAULT_REPO_URL = "https://github.com/aadityasingh9601/Jarvis"
DEFAULT_USER_ID = ""
DEFAULT_WORK_DIR = os.path.join(SCRIPT_DIR, "cloned_repo_update")

CLONE_TIMEOUT = 300
FETCH_TIMEOUT = 180


class MissingLocalContext(RuntimeError):
    """Raised when there is no local index to update."""


def _run(args, cwd=None, timeout=CLONE_TIMEOUT) -> str:
    """Run a git command and return stdout, raising on failure."""
    result = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout


def _remote_head_sha(repo_url: str) -> str:
    """Resolve the remote HEAD sha without downloading any content."""
    out = _run(["git", "ls-remote", repo_url, "HEAD"], timeout=FETCH_TIMEOUT)
    parts = out.split()
    if not parts:
        raise RuntimeError(f"Could not resolve remote HEAD for {repo_url}")
    return parts[0]


def _parse_name_status(diff_output: str) -> tuple:
    """Parse `git diff --name-status` into (added, modified, deleted, renamed).

    Renames come back as 'R100\\told\\tnew'; everything else is a single
    status letter plus a path. Paths that the parser would skip anyway
    (node_modules, dist, venv, ...) are filtered out.
    """
    added, modified, deleted, renamed = [], [], [], []

    for line in diff_output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0].strip()
        letter = status[:1].upper()

        if letter in ("R", "C"):
            if len(parts) < 3:
                continue
            old_path, new_path = parts[1], parts[2]
            if should_skip(new_path):
                continue
            if letter == "C":
                added.append(new_path)
            else:
                renamed.append((old_path, new_path))
        elif letter == "A":
            if len(parts) >= 2 and not should_skip(parts[1]):
                added.append(parts[1])
        elif letter == "M":
            if len(parts) >= 2 and not should_skip(parts[1]):
                modified.append(parts[1])
        elif letter == "D":
            if len(parts) >= 2 and not should_skip(parts[1]):
                deleted.append(parts[1])
        # T (type change) falls back to being re-read as a modification.

    return added, modified, deleted, renamed


def _repo_key(repo_name: str) -> str:
    return (repo_name or "").rstrip("/").rsplit("/", 1)[-1].lower() or "codebase"


def resolve_local_paths(repo_name: str, full_json: str, light_json: str) -> tuple:
    """Fill in the conventional ./codebases filenames when not given."""
    key = _repo_key(repo_name)
    return (
        full_json or os.path.join(DEFAULT_CODEBASES_DIR, f"codebase_{key}.json"),
        light_json or os.path.join(DEFAULT_CODEBASES_DIR, f"codebase_{key}_lightweight.json"),
    )


def _build_lightweight(full: dict) -> dict:
    """Derive the lightweight index from the full index."""
    return {
        "repo_name": full.get("repo_name", ""),
        "description": full.get("description", ""),
        "default_branch": full.get("default_branch", ""),
        "last_commit_sha": full.get("last_commit_sha", ""),
        "file_tree": full.get("file_tree", []),
        "files_metadata": {
            path: {k: entry.get(k) for k in ("language", "size_lines", "imports")}
            for path, entry in full.get("files", {}).items()
        },
    }


def _apply_diff(full: dict, work_dir: str, added, modified, deleted, renamed) -> dict:
    """Apply a git diff to the full index, re-reading only changed files."""
    files = full.setdefault("files", {})
    tree = set(full.get("file_tree", [])) | set(files)

    for path in deleted:
        if files.pop(path, None) is not None:
            tree.discard(path)

    for old_path, new_path in renamed:
        if old_path in files:
            files[new_path] = files.pop(old_path)
        else:
            # Rename of something we never indexed; re-read it below if present.
            if os.path.isfile(os.path.join(work_dir, new_path)):
                added.append(new_path)
        tree.discard(old_path)
        tree.add(new_path)

    re_read = 0
    for path in added + modified:
        disk_path = os.path.join(work_dir, path)
        if not os.path.isfile(disk_path):
            # Reported by git but absent from the checkout (submodule, symlink
            # to nowhere, sparse checkout). Drop any stale copy.
            files.pop(path, None)
            tree.discard(path)
            continue
        files[path] = _parse_file(path, disk_path)
        tree.add(path)
        re_read += 1

    full["file_tree"] = sorted(tree)
    return full


def _persist(full: dict, full_json: str, light_json: str) -> None:
    os.makedirs(os.path.dirname(full_json) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(light_json) or ".", exist_ok=True)
    with open(full_json, "w", encoding="utf-8") as f:
        json.dump(full, f, indent=2)
    with open(light_json, "w", encoding="utf-8") as f:
        json.dump(_build_lightweight(full), f, indent=2)
    print(f"Saved full index      -> {full_json}")
    print(f"Saved lightweight     -> {light_json}")


def _full_rebuild(repo_url: str, work_dir: str, full_json: str, light_json: str,
                  description: str) -> str:
    """Fallback: re-parse the whole checkout and copy it to the target paths."""
    tmp_out = tempfile.mkdtemp(prefix="zz_rebuild_")
    try:
        gen_full, gen_light = parse_repo(work_dir, tmp_out, description)
        shutil.copyfile(gen_full, full_json)
        shutil.copyfile(gen_light, light_json)
    finally:
        shutil.rmtree(tmp_out, ignore_errors=True)
    with open(full_json, "r", encoding="utf-8") as f:
        return json.load(f).get("last_commit_sha", "")


def refresh_dill(light_json: str, user_id: str, storage_path: str) -> str:
    """Bring the session .dill's in-REPL context in line with the new index.

    If the user already has a .dill, its `context_0`/`context` entries are
    replaced with the refreshed metadata while the conversation history is
    preserved. If there is no .dill yet, a fresh one is built.
    """
    import dill

    os.makedirs(storage_path, exist_ok=True)
    dill_path = os.path.join(storage_path, f"{user_id}.dill")

    with open(light_json, "r", encoding="utf-8") as f:
        context = json.load(f)

    if os.path.exists(dill_path):
        with open(dill_path, "rb") as f:
            state = dill.load(f)
        state.repl_namespace["context_0"] = context
        state.repl_namespace["context"] = context
        with open(dill_path, "wb") as f:
            dill.dump(state, f)
        print(f"Refreshed REPL context in existing .dill -> {dill_path}")
    else:
        build_repl_state_from_json(
            lightweight_json=light_json,
            user_id=user_id,
            storage_path=storage_path,
        )
        print(f"Built fresh .dill (none existed)          -> {dill_path}")

    return dill_path


def sync_repo(
    repo_url: str = DEFAULT_REPO_URL,
    repo_name: str = "",
    full_json: str = "",
    light_json: str = "",
    user_id: str = "",
    storage_path: str = DEFAULT_STORAGE_PATH,
    work_dir: str = DEFAULT_WORK_DIR,
    check_only: bool = False,
    allow_full_rebuild: bool = True,
) -> dict:
    """Refresh the local context for `repo_url` to its newest commit."""
    if not repo_name and not full_json:
        raise MissingLocalContext(
            "Pass --repo 'owner/repo' (used to locate the local index and to "
            "address the remote folder) or an explicit --full-json path."
        )

    full_json, light_json = resolve_local_paths(repo_name, full_json, light_json)

    if not os.path.exists(full_json):
        raise MissingLocalContext(
            f"No local codebase index at {full_json}.\n"
            "This updater only runs while a user's session is active, when the "
            "repo context is already on local disk. Nothing to update "
            "incrementally without it.\n"
            "To (re)build a context from scratch, use parse_github_repo.py, or "
            "materialize one with download_repo_context.py."
        )

    with open(full_json, "r", encoding="utf-8") as f:
        full = json.load(f)

    old_sha = full.get("last_commit_sha", "")
    if not old_sha:
        raise MissingLocalContext(
            f"{full_json} has no last_commit_sha, so there is no baseline to "
            "diff against. Re-parse the repo with parse_github_repo.py first."
        )

    # The stored repo_name is authoritative for the remote folder layout.
    repo_name = repo_name or full.get("repo_name", "")
    if not repo_name:
        raise MissingLocalContext(
            f"Could not determine the repo identity from {full_json}; "
            "pass --repo 'owner/repo'."
        )

    latest_sha = _remote_head_sha(repo_url)
    if old_sha == latest_sha:
        print(f"Already up to date at commit {old_sha}.")
        return {"changed": False, "old_sha": old_sha, "new_sha": old_sha}

    print(f"Detected change: {old_sha} -> {latest_sha}")
    if check_only:
        return {"changed": True, "old_sha": old_sha, "new_sha": latest_sha}

    description = full.get("description", "")
    used_fallback = False
    clone_repo(repo_url, work_dir)

    try:
        # Make the previously-indexed commit available so git can diff it
        # against the freshly cloned tip.
        _run(
            ["git", "fetch", "--depth", "1", "origin", old_sha],
            cwd=work_dir, timeout=FETCH_TIMEOUT,
        )
        diff_output = _run(
            ["git", "diff", "--name-status", "-M", old_sha, latest_sha],
            cwd=work_dir,
        )
        added, modified, deleted, renamed = _parse_name_status(diff_output)

        for path in added:
            print(f"  added:    {path}")
        for path in modified:
            print(f"  modified: {path}")
        for path in deleted:
            print(f"  deleted:  {path}")
        for old_path, new_path in renamed:
            print(f"  renamed:  {old_path} -> {new_path}")

        _apply_diff(full, work_dir, added, modified, deleted, renamed)
        full["last_commit_sha"] = latest_sha
        reused = len(full.get("files", {})) - len(added) - len(modified)
        print(f"  reused unchanged content for {reused} file(s).")
    except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        if not allow_full_rebuild:
            raise
        print(f"Incremental diff unavailable ({e}); falling back to full re-parse.")
        used_fallback = True

    if used_fallback:
        latest_sha = _full_rebuild(repo_url, work_dir, full_json, light_json, description)
        with open(full_json, "r", encoding="utf-8") as f:
            full = json.load(f)
        print(f"Re-parsed repo at commit {latest_sha}.")
    else:
        _persist(full, full_json, light_json)

    # Re-upload the index under its new commit folder.
    upload_to_supabase(full_json, light_json, repo_name, latest_sha)

    # Refresh the session state so its context matches the new index, and
    # upload that under the same commit.
    dill_path = ""
    if user_id:
        dill_path = refresh_dill(light_json, user_id, storage_path)
        upload_dill_to_supabase(dill_path, repo_name, latest_sha, light_json)

    shutil.rmtree(work_dir, ignore_errors=True)

    return {
        "changed": True,
        "old_sha": old_sha,
        "new_sha": latest_sha,
        "repo_name": repo_name,
        "full_json": full_json,
        "light_json": light_json,
        "dill": dill_path,
        "rebuilt": used_fallback,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="", help="Repo identity 'owner/repo' (default: read from the local index).")
    parser.add_argument("--repo-url", default="", help=f"Clone URL to fetch from (default: $REPO_URL or {DEFAULT_REPO_URL}).")
    parser.add_argument("--full-json", default="", help="Path to full codebase JSON (default: ./codebases/codebase_<repo>.json).")
    parser.add_argument("--light-json", default="", help="Path to lightweight codebase JSON (default: ./codebases/codebase_<repo>_lightweight.json).")
    parser.add_argument("--user", default=DEFAULT_USER_ID, help="Session user id whose .dill should be refreshed and re-uploaded.")
    parser.add_argument("--storage", default=DEFAULT_STORAGE_PATH, help="Directory holding the session .dill files.")
    parser.add_argument("--work-dir", default=DEFAULT_WORK_DIR, help="Scratch clone directory (deleted afterwards).")
    parser.add_argument("--check", action="store_true", help="Only report whether the context is outdated.")
    parser.add_argument("--no-fallback", action="store_true", help="Fail instead of re-parsing the whole repo if the git diff is unavailable.")
    args = parser.parse_args()

    if not args.repo and not args.repo_url and not args.full_json:
        print("Error: pass --repo 'owner/repo' (and optionally --repo-url).")
        return 1

    repo_name = args.repo
    repo_url = args.repo_url or os.getenv("REPO_URL") or (
        f"https://github.com/{repo_name}" if repo_name else DEFAULT_REPO_URL
    )

    try:
        result = sync_repo(
            repo_url=repo_url,
            repo_name=repo_name,
            full_json=args.full_json,
            light_json=args.light_json,
            user_id=args.user,
            storage_path=args.storage,
            work_dir=args.work_dir,
            check_only=args.check,
            allow_full_rebuild=not args.no_fallback,
        )
    except MissingLocalContext as e:
        print(f"Error: {e}")
        return 1
    except Exception as e:
        print(f"Update failed: {e}")
        return 1

    if args.check:
        if not result["changed"]:
            print("UP_TO_DATE")
            return 0
        print(f"OUTDATED ({result['old_sha']} -> {result['new_sha']})")
        return 1

    if not result["changed"]:
        return 0
    print(f"Context updated to commit {result['new_sha']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
