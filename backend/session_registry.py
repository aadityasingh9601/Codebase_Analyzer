"""
session_registry.py — one live RLM per (user, repo, sha), lazily built and
evicted when idle.

Why per user and not shared: an RLM's `completion()` call starts an LM handler
socket and points the shared environment at it — `environment.update_handler_address((host, port))`.
Two overlapping completions on one instance repoint that address at each other,
so user A's sub-LLM calls get routed to user B's handler. A single instance
cannot safely serve two users. Different users must get different instances.

What IS shared: the parsed index, via `session_index`, and the local files
behind it, via `local_index`. Each REPL's `context` is a `SharedContext`
proxy rather than a dict copy, so N users on one repository hold one copy of
the metadata, not N.

The per-session `asyncio.Lock` exists for a narrower reason than the registry:
it stops ONE user from hitting the same instance twice at once — a double
click, two tabs, a client retry. It does not coordinate between users, who
are already isolated by having their own instance.

Concurrency is additionally bounded by a global semaphore, because a query
(routed either way) makes outbound model calls and OpenRouter rate-limits.

Usage:
    from session_registry import get_or_create, evict_idle, cleanup_all, stats

    handle = await get_or_create(user_id, repo_name, sha)
    result = await handle.ask(question)
"""

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import session_index
from session_index import close_session, set_default_session_id, reset_default_session_id
from rlm_config import DEFAULT_MODEL

# Hard cap on queries making model calls at once. Not about traffic volume —
# the app is new — but each RLM completion can hold a thread for up to
# max_timeout=240s, and unbounded concurrency would exhaust worker threads and
# trip the provider's rate limit.
MAX_CONCURRENT_QUERIES = 4

# Idle eviction thresholds (seconds). Two tiers, deliberately different:
# rebuilding a parsed index means re-downloading and re-parsing a 50MB JSON,
# while rebuilding an RLM is cheap. A short RLM TTL with a long index TTL
# means a user returning from a break almost always finds their index warm.
RLM_IDLE_SECONDS = 600
INDEX_IDLE_SECONDS = 3600

MAX_SESSIONS = 32

_query_slots: Optional[asyncio.Semaphore] = None


def _slots() -> asyncio.Semaphore:
    global _query_slots
    if _query_slots is None:
        _query_slots = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)
    return _query_slots


def _session_key(user_id: str, repo_name: str, sha: str) -> str:
    """session_index key: unique per user AND per repository at one commit."""
    owner_repo = repo_name.strip().strip("/")
    return f"{user_id}:{owner_repo}@{(sha or '')[:16]}"


@dataclass
class SessionHandle:
    """Live state for one user's view of one repository at one commit."""

    user_id: str
    repo_name: str
    sha: str
    session_id: str
    full_path: str = ""
    light_path: str = ""
    rlm: Optional[object] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    queries: int = 0

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used

    async def ask(self, question: str):
        """Answer `question` about this repository.

        Materialises files, parses the index, and builds the RLM on first use,
        each in a worker thread so the event loop stays responsive. The
        per-session lock serialises calls against this handle; the global
        semaphore bounds how many queries are talking to the model at once.
        """
        from query_router import RouterResult, route_and_answer
        from starlette.concurrency import run_in_threadpool

        question = (question or "").strip()
        if not question:
            return RouterResult(
                answer=None,
                tier="ERROR",
                reason="empty question",
                answer_source="validation",
                error="question must not be empty",
            )

        self.last_used = time.monotonic()

        async with self.lock:
            self.last_used = time.monotonic()
            full_path, light_path = await run_in_threadpool(
                _materialise, self.repo_name, self.sha
            )
            self.full_path, self.light_path = full_path, light_path
            await run_in_threadpool(
                session_index.acquire_session, self.session_id, full_path, light_path
            )
            if self.rlm is None:
                self.rlm = await run_in_threadpool(_build_rlm, self)
            self.queries += 1
            self.last_used = time.monotonic()

            async with _slots():
                result = await run_in_threadpool(
                    _run_query, self.session_id, question, self.rlm
                )

        self.last_used = time.monotonic()
        return result

    def release(self) -> None:
        """Tear down this handle. Caller must not be running a query."""
        if self.rlm is not None:
            try:
                self.rlm.close()
            except Exception:
                pass
            self.rlm = None
        close_session(self.session_id)
        if self.full_path:
            from local_index import release_index

            try:
                release_index(self.repo_name, self.sha)
            except Exception:
                pass
            self.full_path = ""
            self.light_path = ""


def _materialise(repo_name: str, sha: str) -> tuple:
    from local_index import ensure_index

    return ensure_index(repo_name, sha)


def _build_rlm(handle: SessionHandle):
    from rlm_config import create_rlm

    return create_rlm(
        full_json_path=handle.full_path,
        light_json_path=handle.light_path,
        session_id=handle.session_id,
        model_name=DEFAULT_MODEL,
    )


def _run_query(session_id: str, question: str, rlm) -> object:
    """Worker-thread entry point for `route_and_answer`.

    Sets the session routing ContextVar HERE rather than in the request
    coroutine: this function executes entirely inside the worker thread, so
    the value is guaranteed to be visible to `current_session()` for every
    nested call (`_load_index`, `_file_tree`, the exec'd REPL helpers) and is
    discarded when the call finishes. Setting it only in the async caller
    would leave `current_session()` unrouted.
    """
    from query_router import route_and_answer

    token = set_default_session_id(session_id)
    try:
        return route_and_answer(question, rlm=rlm)
    finally:
        reset_default_session_id(token)


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

_handles: dict[str, SessionHandle] = {}
# threading lock, not asyncio: the janitor runs as a plain thread and
# `cleanup_all` may be called from a sync shutdown hook.
_registry_lock = threading.RLock()


async def get_or_create(user_id: str, repo_name: str, sha: str) -> SessionHandle:
    """Return this user's handle for `repo_name` at `sha`, creating it if new.

    Only bookkeeping runs here — no downloads, no parsing, no model client.
    Those happen on the first `ask()`, off the event loop.
    """
    repo_name = (repo_name or "").strip().strip("/")
    sha = (sha or "").strip()
    if not user_id:
        raise ValueError("user_id is required")
    if not repo_name:
        raise ValueError("repo_name is required")
    if not sha:
        raise ValueError("commit sha is required")

    session_id = _session_key(user_id, repo_name, sha)

    with _registry_lock:
        handle = _handles.get(session_id)
        if handle is None:
            handle = SessionHandle(
                user_id=user_id,
                repo_name=repo_name,
                sha=sha,
                session_id=session_id,
            )
            _handles[session_id] = handle
        handle.last_used = time.monotonic()
        over_capacity = len(_handles) > MAX_SESSIONS

    if over_capacity:
        # Reclaim the coldest handle we can safely close.
        evict_idle(rlm_idle_seconds=0.0, index_idle_seconds=INDEX_IDLE_SECONDS)
    return handle


def get(session_id: str) -> Optional[SessionHandle]:
    with _registry_lock:
        return _handles.get(session_id)


def handles_for_user(user_id: str) -> list:
    """Live handles for one user, most recently used first."""
    with _registry_lock:
        matches = [h for h in _handles.values() if h.user_id == user_id]
    return sorted(matches, key=lambda h: h.last_used, reverse=True)


def find_handle(user_id: str, repo_name: str, sha: str = "") -> Optional[SessionHandle]:
    """Live handle for this user+repo, ignoring sha when it is not known."""
    repo_name = (repo_name or "").strip().strip("/")
    with _registry_lock:
        for handle in _handles.values():
            if handle.user_id != user_id or handle.repo_name != repo_name:
                continue
            if not sha or handle.sha == sha:
                return handle
    return None


def evict_idle(
    rlm_idle_seconds: float = RLM_IDLE_SECONDS,
    index_idle_seconds: float = INDEX_IDLE_SECONDS,
) -> list[str]:
    """Close RLM handles idle longer than `rlm_idle_seconds`, then trim the
    parsed indexes of everything left.

    Handles with a query in flight are skipped: `SessionHandle.lock` is held
    for the whole query, so a locked handle is never safe to tear down.

    The two tiers have deliberately different lifetimes. Freeing an RLM is
    cheap; freeing a parsed index means re-downloading and re-parsing up to
    50MB on the next query. So `rlm_idle_seconds` is short (a user stepping
    away loses only conversation state) and `index_idle_seconds` is long (a
    returning user still finds their index resident).

    `rlm_idle_seconds=0.0` evicts every unlocked handle — the over-capacity
    path and shutdown both use it.
    """
    evicted: list[str] = []
    to_release: list[SessionHandle] = []

    with _registry_lock:
        if rlm_idle_seconds <= 0.0:
            victims = [
                h for h in list(_handles.values()) if not h.lock.locked()
            ]
        else:
            victims = [
                h
                for h in list(_handles.values())
                if h.idle_seconds >= rlm_idle_seconds and not h.lock.locked()
            ]

        for handle in victims:
            if _handles.pop(handle.session_id, None) is not None:
                evicted.append(handle.session_id)
                to_release.append(handle)

        # Still over capacity after idle eviction: drop the coldest unlocked.
        if len(_handles) > MAX_SESSIONS:
            spare = sorted(
                (h for h in _handles.values() if not h.lock.locked()),
                key=lambda h: h.last_used,
            )
            excess = len(_handles) - MAX_SESSIONS
            for handle in spare[:excess]:
                _handles.pop(handle.session_id, None)
                evicted.append(handle.session_id)
                to_release.append(handle)

    for handle in to_release:
        handle.release()

    # Safety net for sessions closed by a crashed request rather than by us.
    if index_idle_seconds > 0.0:
        session_index.evict_idle(index_idle_seconds)

    return evicted


def cleanup_all() -> int:
    """Release every handle, index, and local file. Use on shutdown."""
    with _registry_lock:
        handles = list(_handles.values())
        _handles.clear()
    for handle in handles:
        if handle.lock.locked():
            continue
        handle.release()

    session_index.close_all()

    from local_index import cleanup_all as cleanup_indexes

    return cleanup_indexes()


def stats() -> dict:
    with _registry_lock:
        handles = list(_handles.values())
        locked = sum(1 for h in handles if h.lock.locked())
        return {
            "live_sessions": len(handles),
            "queries_in_flight": locked,
            "max_concurrent_queries": MAX_CONCURRENT_QUERIES,
            "max_sessions": MAX_SESSIONS,
            "rlm_idle_seconds": RLM_IDLE_SECONDS,
            "index_idle_seconds": INDEX_IDLE_SECONDS,
            "total_queries": sum(h.queries for h in handles),
            "sessions": [
                {
                    "session_id": h.session_id,
                    "repo": h.repo_name,
                    "sha": h.sha[:7],
                    "queries": h.queries,
                    "idle_seconds": round(h.idle_seconds, 1),
                    "has_rlm": h.rlm is not None,
                }
                for h in sorted(handles, key=lambda x: x.last_used, reverse=True)
            ],
        }

