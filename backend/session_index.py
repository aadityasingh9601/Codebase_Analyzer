"""
session_index.py — session-scoped lifecycle for parsed codebase indexes.

WHY THIS EXISTS
---------------
The 50 MB `codebase.json` was being read differently by every caller:

  - `codebase_helpers._load_full_index()` re-read and re-parsed the whole file
    on EVERY helper call, so a session that fetched 30 files parsed 50 MB
    thirty times. Pure waste, and a latency spike per call.
  - `query_router._load_index()/_load_full()` used `@lru_cache(maxsize=1)`,
    which caches correctly but pins the parsed dict for the life of the
    process. One repo = one pinned index forever, with no way to release it
    when the last user walks away.

Fixing the first with a per-session cache is not enough: if ten users open the
same repo you get ten independent 50 MB copies (500 MB) for identical data.
So indexes here are SHARED per (path, mtime) and REFERENCE COUNTED. Ten
sessions on one repo hold one copy; the last one to close frees it.

Design notes:
  - Shared data is returned by reference, not copied. Copying would defeat the
    purpose (a 50 MB deep copy per call). Callers must treat the returned dicts
    as READ-ONLY. All helpers in this codebase only read from them.
  - `mtime` is part of the cache key, so when `update_github_repo.py` rewrites
    the JSON the next session picks up new content automatically instead of
    serving a stale index forever.
  - The registry, not the handle, owns each reference. `acquire_session` is
    cheap to call repeatedly, and a discarded handle never tears down a live
    session. Sessions that are never closed explicitly are bounded by
    `evict_idle()`, which the server should call periodically.

Server integration sketch:
    import session_index

    @app.post("/query")
    def query(user_id: str, ...):
        session_index.set_default_session_id(user_id)   # route this request
        try:
            return route_and_answer(question)
        finally:
            session_index.close_session(user_id)        # free on completion

    # background task
    while True:
        session_index.evict_idle(max_idle_seconds=1800)
        time.sleep(60)

Usage:
    from session_index import acquire_session, current_session, close_all

    session = acquire_session("user123", full_path, light_path)
    session.get_file_content("src/app.py")

    close_session("user123")            # frees RAM if this was the last user
"""

import gc
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "CodebaseSession",
    "acquire_session",
    "close_session",
    "close_all",
    "evict_idle",
    "list_sessions",
    "current_session",
    "current_session_id",
    "set_default_session_id",
    "shared_index_stats",
]


# --------------------------------------------------------------------------
# Shared, ref-counted index storage
# --------------------------------------------------------------------------


@dataclass
class _IndexEntry:
    """One parsed index pair, shared by every session using the same files."""

    full: dict
    light: dict
    refs: int = 0

    @property
    def approx_bytes(self) -> int:
        return _deep_size(self.full) + _deep_size(self.light)


def _deep_size(obj, _seen=None) -> int:
    """Recursively estimate the retained size of a parsed JSON structure.

    Counts each object once (dicts hold one object per key that also appears in
    values, so naive summation badly over-counts). Used only for reporting.
    """
    if _seen is None:
        _seen = set()
    obj_id = id(obj)
    if obj_id in _seen:
        return 0
    _seen.add(obj_id)

    size = sys_getsizeof(obj)
    if isinstance(obj, dict):
        for key, value in obj.items():
            size += _deep_size(key, _seen) + _deep_size(value, _seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            size += _deep_size(item, _seen)
    return size


def sys_getsizeof(obj) -> int:
    import sys

    return sys.getsizeof(obj)


# key: (full_path, full_mtime, light_path, light_mtime) -> _IndexEntry
_entries: dict[tuple, _IndexEntry] = {}
# session_id -> key into _entries
_sessions: dict[str, tuple] = {}
# session_id -> monotonic timestamp of last use, for idle eviction
_last_used: dict[str, float] = {}
# Guards every mutation. Index parsing is slow and expensive; two threads
# racing to load the same repo would otherwise parse it twice and one copy
# would leak.
_lock = threading.RLock()

DEFAULT_SESSION_ID = "__default__"
_default_session_id = DEFAULT_SESSION_ID


def set_default_session_id(session_id: Optional[str]) -> None:
    """Route the unscoped `current_session()` accessor to a named session.

    The server will call this at request time so the stateless helpers in
    `query_router` operate on the current user's session rather than one
    process-wide fallback.
    """
    global _default_session_id
    _default_session_id = session_id or DEFAULT_SESSION_ID


def current_session_id() -> str:
    """The session id that `current_session()` and `close_session()` target."""
    return _default_session_id


def _cache_key(full_path: str, light_path: str) -> Optional[tuple]:
    """Build a cache key from paths + mtimes, or None if a file is missing."""
    try:
        full_st = os.stat(full_path)
        light_st = os.stat(light_path)
    except OSError:
        return None
    return (
        os.path.abspath(full_path),
        full_st.st_mtime_ns,
        os.path.abspath(light_path),
        light_st.st_mtime_ns,
    )


def _load_entry(key: tuple) -> _IndexEntry:
    """Return the entry for `key`, parsing it if this is the first request.

    Caller must hold `_lock`.
    """
    entry = _entries.get(key)
    if entry is not None:
        return entry

    full_path, _, light_path, _ = key
    with open(full_path, "r") as f:
        full = json.load(f)
    with open(light_path, "r") as f:
        light = json.load(f)

    entry = _IndexEntry(full=full, light=light)
    _entries[key] = entry
    return entry


def _touch(session_id: str) -> None:
    """Record that `session_id` was just used, for idle-based eviction."""
    _last_used[session_id] = time.monotonic()


def _release_key(key: tuple) -> None:
    """Drop one reference to `key`, freeing it when the count reaches zero.

    Caller must hold `_lock`.
    """
    entry = _entries.get(key)
    if entry is None:
        return
    entry.refs -= 1
    if entry.refs > 0:
        return

    _entries.pop(key, None)
    # Drop our own references, then ask the collector to reclaim the nested
    # dicts/strings now rather than at some arbitrary later point.
    entry.full = {}
    entry.light = {}
    del entry
    gc.collect()


# --------------------------------------------------------------------------
# Session handle
# --------------------------------------------------------------------------


class CodebaseSession:
    """A user's live view of one repo's parsed indexes.

    Holds a reference on the shared `_IndexEntry`; the memory is actually
    released when the last session for that repo is closed.
    """

    __slots__ = ("session_id", "_key", "_entry")

    def __init__(self, session_id: str, key: tuple, entry: _IndexEntry):
        self.session_id = session_id
        self._key = key
        self._entry = entry

    # -- data access -------------------------------------------------

    def get_file_content(self, path: str) -> str:
        """Full content of one file. Same contract as the old helper."""
        file_data = self._entry.full.get("files", {}).get(path)
        if file_data is None:
            return f"File not found: {path}"
        return file_data.get("content", f"No content field for: {path}")

    def get_multiple_files(self, paths: list) -> dict:
        """Contents of several files at once."""
        files = self._entry.full.get("files", {})
        return {p: files.get(p, {}).get("content", "Not found") for p in paths}

    def search_files_by_keyword(self, keyword: str) -> list:
        """Paths whose content contains `keyword` (case-insensitive)."""
        needle = keyword.lower()
        return [
            path
            for path, data in self._entry.full.get("files", {}).items()
            if needle in data.get("content", "").lower()
        ]

    def light_metadata(self) -> dict:
        """The lightweight index (file tree, per-file metadata). Read-only."""
        return self._entry.light

    def full_index(self) -> dict:
        """The full index. Read-only — mutating it corrupts other sessions."""
        return self._entry.full

    def file_tree(self) -> list:
        return self._entry.light.get("file_tree", [])

    @property
    def last_commit_sha(self) -> str:
        return self._entry.light.get("last_commit_sha", "")

    # -- lifecycle ---------------------------------------------------

    def close(self) -> None:
        """Release this session's reference. Idempotent.

        Note this is NOT called from `__del__`. The registry in this module is
        the owner of the reference: a short-lived handle that gets garbage
        collected (as happens on every `acquire_session` call) must not tear
        down a session that is still in use. Leaked sessions are instead
        reclaimed by `close_session`, `close_all`, or `evict_idle`.
        """
        with _lock:
            if self._key is None:
                return
            key, self._key = self._key, None
            self._entry = _IndexEntry(full={}, light={})
            if _sessions.get(self.session_id) == key:
                _sessions.pop(self.session_id, None)
            _release_key(key)

    def __enter__(self) -> "CodebaseSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def acquire_session(
    session_id: str,
    full_json_path: str,
    light_json_path: str = "",
    ) -> CodebaseSession:
    """Get (or create) the session for `session_id` and pin its indexes.

    Calling this twice for the same session is a no-op beyond bumping the
    reference count, so it is safe to call on every request.

    If `light_json_path` is omitted it is inferred from the full path using the
    `codebase_<repo>.json` / `codebase_<repo>_lightweight.json` convention.
    """
    if not light_json_path:
        light_json_path = _guess_light_path(full_json_path)

    key = _cache_key(full_json_path, light_json_path)
    if key is None:
        raise FileNotFoundError(
            f"Codebase index not found for session {session_id!r}: "
            f"{full_json_path} (light: {light_json_path})"
        )

    with _lock:
        previous = _sessions.get(session_id)
        if previous == key:
            # Already pinned. Re-acquiring is a no-op, NOT a new reference:
            # `acquire_session` is called on every helper invocation, so
            # counting each call would make the refcount grow without bound
            # and the index would never be freed.
            entry = _entries.get(key)
            if entry is not None:
                _touch(session_id)
                return CodebaseSession(session_id, key, entry)

        if previous is not None:
            # The underlying files changed since this session was opened, or
            # the session was pointed at a different repo. Drop the old pin.
            _release_key(previous)

        entry = _load_entry(key)
        entry.refs += 1
        _sessions[session_id] = key
        _touch(session_id)
        return CodebaseSession(session_id, key, entry)


def _guess_light_path(full_json_path: str) -> str:
    directory = os.path.dirname(os.path.abspath(full_json_path))
    base = os.path.basename(full_json_path)
    if base.endswith(".json"):
        base = base[: -len(".json")]
    if base.endswith("_lightweight"):
        base = base[: -len("_lightweight")]
    return os.path.join(directory, f"{base}_lightweight.json")


def close_session(session_id: Optional[str] = None) -> None:
    """End a session, freeing its indexes if it was the last user of the repo.

    Omitting `session_id` closes whichever session `current_session()` is
    currently routed to, which is what the server's request teardown wants.
    """
    with _lock:
        target = session_id or _default_session_id
        key = _sessions.pop(target, None)
        _last_used.pop(target, None)
        if key is None:
            return
        _release_key(key)


def evict_idle(max_idle_seconds: float) -> list[str]:
    """Close sessions unused for longer than `max_idle_seconds`.

    This is the safety net that bounds memory when callers forget to close
    (a crashed request, a client that vanished without a disconnect event).
    Returns the session ids that were evicted.

    Active sessions are never touched: a session is only idle if it has not
    been acquired within the window.
    """
    now = time.monotonic()
    evicted = []
    with _lock:
        for session_id, stamp in list(_last_used.items()):
            if now - stamp < max_idle_seconds:
                continue
            close_session(session_id)
            evicted.append(session_id)
    return evicted


def close_all() -> None:
    """Release every session. Use on server shutdown."""
    with _lock:
        for key in list(_sessions.values()):
            _release_key(key)
        _sessions.clear()
        _entries.clear()
        _last_used.clear()
        gc.collect()


def list_sessions() -> list[str]:
    with _lock:
        return sorted(_sessions)


def current_session(full_json_path: str = "", light_json_path: str = "") -> CodebaseSession:
    """Session for the currently-routed request, created on demand.

    This preserves the stateless call style used by `query_router` and the
    exec'd REPL helpers, while routing to a real per-user session so the
    pinning is still bounded. The server calls `set_default_session_id()`.
    """
    if not full_json_path:
        from rlm_config import CODEBASE_JSON_ABS, LIGHTWEIGHT_JSON_ABS

        full_json_path = CODEBASE_JSON_ABS
        light_json_path = light_json_path or LIGHTWEIGHT_JSON_ABS

    return acquire_session(_default_session_id, full_json_path, light_json_path)


def shared_index_stats() -> dict:
    """Introspection for the server's health endpoint and tests."""
    with _lock:
        return {
            "live_entries": len(_entries),
            "sessions": len(_sessions),
            "refcounts": {os.path.basename(k[0]): e.refs for k, e in _entries.items()},
            "approx_retained_bytes": sum(e.approx_bytes for e in _entries.values()),
        }
