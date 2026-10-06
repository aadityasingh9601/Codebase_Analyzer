"""
shared_context.py — the model's `context` variable.

Defined in a real module rather than inside the REPL's `setup_code` because
the sandbox's `__builtins__` does not include `__build_class__`: a `class`
statement executed there raises NameError, and `LocalREPL.execute_code` swallows
exceptions from `setup_code` (they land in a stderr string that `__init__`
discards). The failure mode is a REPL where `context` silently never exists.

The model is told `context` is a dict with `repo_name`, `description`,
`file_tree` and `files_metadata`, and reads it as `context['file_tree']`. This
class satisfies exactly that surface by delegating every access to a live
`CodebaseSession`, so it holds no data of its own.

Why not just assign the dict: one dict per session means one copy of the
metadata per user. Ten users on the same repository would hold ten copies of a
2 MB index (22 MB for cal.diy) and `session_index`'s reference-counted sharing
would buy nothing for the RLM tier. Here every user resolves the same shared
parse, and the per-instance cost is one callable.
"""

from typing import Callable, Optional


class SharedContext:
    """Read-only mapping view over the shared lightweight index.

    Read-only on purpose: the underlying parse is referenced by other
    sessions, so mutating it would corrupt them. All helpers read from it.
    """

    __slots__ = ("_resolver",)

    def __init__(self, resolver: Optional[Callable] = None):
        """
        Args:
            resolver: zero-arg callable returning a `CodebaseSession`. When
                omitted the session is resolved from the ContextVar routing
                set by the query path (`session_registry._run_query`).
        """
        self._resolver = resolver

    def _data(self) -> dict:
        if self._resolver is not None:
            return self._resolver().light_metadata()
        from session_index import current_session

        return current_session().light_metadata()

    def __getitem__(self, key):
        return self._data()[key]

    def get(self, key, default=None):
        return self._data().get(key, default)

    def keys(self):
        return self._data().keys()

    def values(self):
        return self._data().values()

    def items(self):
        return self._data().items()

    def __contains__(self, key):
        return key in self._data()

    def __iter__(self):
        return iter(self._data())

    def __len__(self):
        return len(self._data())

    def __eq__(self, other):
        if isinstance(other, SharedContext):
            return True
        if isinstance(other, dict):
            return self._data() == other
        return NotImplemented

    def __repr__(self):
        try:
            data = self._data()
            return (
                f"<SharedContext repo={data.get('repo_name')!r} "
                f"files={len(data.get('file_tree', []))}>"
            )
        except Exception as exc:
            return f"<SharedContext unavailable: {type(exc).__name__}: {exc}>"
