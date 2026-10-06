"""
local_index.py — transient local copies of a repository's index files.

`session_index` shares the *parsed* index across users, but it still needs a
file on disk to parse: `_cache_key` calls `os.stat` and `_load_entry` calls
`json.load` on a path. So "keep the index in memory only" cannot literally
mean "never write a file".

This module resolves that tension. A repository's index is downloaded once
into a `tempfile.mkdtemp()` directory and shared by refcount, then the whole
directory is deleted when the last session for that repository lets go (or on
shutdown). Nothing survives idle on disk: there is no permanent cache to go
stale when a repo is re-indexed under the same SHA, and no leftover 50 MB
blobs after a deploy.

Refcounted per (owner, repo, sha) so ten users on one repository share one
download and one directory.

Usage:
    from local_index import ensure_index, release_index, cleanup_all

    full_path, light_path = ensure_index("pallets", "click", sha)
    ...
    release_index("pallets", "click", sha)
"""

import os
import shutil
import tempfile
import threading

from repo_storage import (
    check_size_limit,
    split_repo,
    storage_path_json,
    storage_path_light_json,
    stream_download,
)

FULL_NAME = "codebase.json"
LIGHT_NAME = "codebase_lightweight.json"

_lock = threading.RLock()
# (owner, repo, sha) -> {"dir": str, "full": str, "light": str, "refs": int}
_entries: dict[tuple, dict] = {}

# Root directory for every index we materialise. Created lazily so merely
# importing this module costs nothing.
_root: dict[str, str] = {}

# key -> threading.Lock serialising the download of that specific repo@sha.
# Entries are removed when the last ref is released so this cannot grow
# unboundedly as repos come and go.
_download_locks: dict[tuple, threading.Lock] = {}


def _root_dir() -> str:
    with _lock:
        if "path" not in _root:
            _root["path"] = tempfile.mkdtemp(prefix="repochat-index-")
        return _root["path"]


def ensure_index(repo_name: str, sha: str) -> tuple:
    """Download (once) and return `(full_path, light_path)` for this repo@sha.

    Blocks until the transfer completes. Callers that must not block the
    event loop should run this in a worker thread.

    Concurrency: there is a per-key lock around the download itself, so a
    thundering herd on a cold repository all share one transfer (exactly two
    objects downloaded) instead of racing to write the same files. Different
    repositories download in parallel — the lock is not global. Completed
    entries short-circuit before and after the lock, so steady-state callers
    never touch the disk.

    During a download the bytes land in a caller-unique staging directory
    (`stream_download` writes a fixed `<name>.part` sibling, so sharing a
    directory between writers would corrupt both); the winner atomically
    renames its staging directory into place, the rest discard theirs.

    Raises:
        FileNotFoundError: no such repo@sha in the bucket.
        RuntimeError: download failed, or the full index exceeds the size cap.
    """
    owner, repo = split_repo(repo_name)
    sha = (sha or "").strip()
    if not sha:
        raise ValueError("commit sha is required to materialise an index")

    key = (owner, repo, sha)

    with _lock:
        existing = _entries.get(key)
        if existing is not None:
            existing["refs"] += 1
            return existing["full"], existing["light"]
        dl_lock = _download_locks.setdefault(key, threading.Lock())

    with dl_lock:
        with _lock:
            # A waiter may have completed the download while we were blocked.
            existing = _entries.get(key)
            if existing is not None:
                existing["refs"] += 1
                return existing["full"], existing["light"]

        staging = tempfile.mkdtemp(dir=_root_dir(), prefix=f"{owner}__{repo}__{sha}.")
        full_path = os.path.join(staging, FULL_NAME)
        light_path = os.path.join(staging, LIGHT_NAME)

        try:
            stream_download(storage_path_json(owner, repo, sha), full_path)
            check_size_limit(full_path)
            stream_download(storage_path_light_json(owner, repo, sha), light_path)
            for path in (full_path, light_path):
                if os.path.getsize(path) == 0:
                    raise RuntimeError(f"Empty index object for {repo_name}@{sha[:7]}")
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        final_dir = os.path.join(_root_dir(), f"{owner}__{repo}__{sha}")

        with _lock:
            existing = _entries.get(key)
            if existing is not None:
                # Someone else finished first while we were downloading.
                shutil.rmtree(staging, ignore_errors=True)
                existing["refs"] += 1
                return existing["full"], existing["light"]

            # A stale directory may survive a crash between download and
            # registry write; never adopt unknown bytes.
            if os.path.isdir(final_dir):
                shutil.rmtree(final_dir, ignore_errors=True)
            os.replace(staging, final_dir)

            full_path = os.path.join(final_dir, FULL_NAME)
            light_path = os.path.join(final_dir, LIGHT_NAME)
            _entries[key] = {
                "dir": final_dir,
                "full": full_path,
                "light": light_path,
                "refs": 1,
            }
            return full_path, light_path


def release_index(repo_name: str, sha: str) -> bool:
    """Drop one reference; delete the directory when the count reaches zero.

    Returns True if this was the last reference and the files were removed.
    """
    owner, repo = split_repo(repo_name)
    sha = (sha or "").strip()
    key = (owner, repo, sha)

    with _lock:
        entry = _entries.get(key)
        if entry is None:
            return False
        entry["refs"] -= 1
        if entry["refs"] > 0:
            return False
        _entries.pop(key, None)
        _download_locks.pop(key, None)

    shutil.rmtree(entry["dir"], ignore_errors=True)
    return True


def is_materialised(repo_name: str, sha: str) -> bool:
    with _lock:
        return (split_repo(repo_name)[0], split_repo(repo_name)[1], sha) in _entries


def stats() -> dict:
    with _lock:
        return {
            "live_indexes": len(_entries),
            "refs": {
                f"{k[0]}/{k[1]}@{k[2][:7]}": v["refs"] for k, v in _entries.items()
            },
            "root": _root.get("path", ""),
        }


def cleanup_all() -> int:
    """Remove every materialised index and the root directory. On shutdown."""
    with _lock:
        dirs = [v["dir"] for v in _entries.values()]
        _entries.clear()
        _download_locks.clear()
        root = _root.pop("path", "")
    removed = 0
    for directory in dirs:
        if os.path.isdir(directory):
            shutil.rmtree(directory, ignore_errors=True)
            removed += 1
    if root and os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
    return removed
