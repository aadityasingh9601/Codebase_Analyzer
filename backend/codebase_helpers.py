"""
Codebase helper functions + metadata loader for RLM.

These functions are injected into the REPL environment via the RLM's
`environment_kwargs["setup_code"]`. They are re-defined on EVERY session
(fresh and restored), so they do NOT need to be serialized into the
PersistentSession state — they always exist.

The full-codebase JSON path is set BEFORE this file is exec'd, via a
module-level line prepended in rlm_config.py: `_CODEBASE_JSON = <abs path>`.
Inside exec() there is no __file__, so we cannot resolve it here.

Index loading is delegated to session_index, which parses each index once and
shares it across every session on the same repo. Previously each helper call
re-read and re-parsed the whole 50MB JSON, so a session reading 30 files did
30 full parses. `close_codebase_session()` releases this session's reference.
"""

import json
import os
import sys

# Where the full codebase JSON lives. Set before exec via RLM.py setup_code:
#   _CODEBASE_JSON = r"/abs/path/to/codebase_paytm.json"
# Only default (from env) if it was NOT already defined in the exec namespace.
try:
    _CODEBASE_JSON
except NameError:
    _CODEBASE_JSON = os.environ.get("CODEBASE_JSON", "")

# The lightweight index sits next to the full one and is derived from the full
# path when RLM.py does not inject it explicitly.
try:
    _LIGHTWEIGHT_JSON
except NameError:
    _LIGHTWEIGHT_JSON = os.environ.get("CODEBASE_LIGHTWEIGHT_JSON", "")
if not _LIGHTWEIGHT_JSON:
    # Inside exec() there is no __file__, so fall back to the path of the
    # module that injected _CODEBASE_JSON, then to the process cwd.
    try:
        _injected_from = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        _injected_from = os.getcwd()
    if _injected_from not in sys.path:
        sys.path.insert(0, _injected_from)

    from session_index import _guess_light_path

    _LIGHTWEIGHT_JSON = _guess_light_path(_CODEBASE_JSON)


def load_codebase_metadata() -> dict:
    """Load the lightweight repo metadata (file tree, imports, line counts)."""
    light_path = os.environ.get(
        "CODEBASE_LIGHTWEIGHT_JSON",
        os.path.join(os.path.dirname(os.path.abspath(_CODEBASE_JSON)), "codebase_paytm_lightweight.json"),
    )
    with open(light_path, "r") as f:
        return json.load(f)


# --- session-scoped index access ------------------------------------------
#
# These helpers used to re-read and re-parse the whole 50MB codebase JSON on
# every single call, so a session fetching 30 files parsed it 30 times. The
# index is now parsed once per session and shared, ref-counted, via
# session_index (see that module for the full rationale).
#
# `_session` is resolved per call rather than captured at setup time so the
# REPL namespace stays picklable and the server can re-route a live session
# without re-running setup_code.
try:
    _CODEBASE_SESSION_ID
except NameError:
    _CODEBASE_SESSION_ID = os.environ.get("CODEBASE_SESSION_ID", "")

_SESSION_ID = _CODEBASE_SESSION_ID or "repl"


def _get_session():
    from session_index import acquire_session

    return acquire_session(_SESSION_ID, _CODEBASE_JSON, _LIGHTWEIGHT_JSON)


def get_file_content(path: str) -> str:
    """Read full content of a specific file from the codebase JSON."""
    try:
        return _get_session().get_file_content(path)
    except Exception as e:
        return f"Error reading file: {e}"


def get_multiple_files(paths: list) -> dict:
    """Read multiple files at once from the codebase JSON."""
    try:
        return _get_session().get_multiple_files(paths)
    except Exception as e:
        return {p: f"Error: {e}" for p in paths}


def search_files_by_keyword(keyword: str) -> list:
    """Search all file contents for a keyword and return matching paths."""
    try:
        return _get_session().search_files_by_keyword(keyword)
    except Exception as e:
        return [f"Search error: {e}"]


def close_codebase_session() -> None:
    """Release this session's index reference, freeing the RAM if it was last.

    Not part of the model's tool surface — the server/REPL owner calls it when
    a session ends so the 50MB index is not pinned for the process lifetime.
    """
    from session_index import close_session

    close_session(_SESSION_ID)
