"""
Codebase helper functions + metadata loader for RLM.

These functions are injected into the REPL environment via the RLM's
`environment_kwargs["setup_code"]`. They are re-defined on EVERY session
(fresh and restored), so they do NOT need to be serialized into the
PersistentSession state — they always exist.

Two modes, both set by lines prepended in rlm_config.SETUP_CODE before this
file is exec'd:

  LOCAL_MODE=False (legacy / default)
    Reads files from the full codebase JSON on disk (_CODEBASE_JSON).
  LOCAL_MODE=True
    Reads files DIRECTLY from the local filesystem via local_repo_index's
    guarded readers (soft guardrails: skip lists, root containment, binary
    and size caps). A local add_context/repl_tmp read jail is layered on by
    repl_guardrail so direct `open` calls are hard-guarded too.

Inside exec() there is no __file__, so all absolutes are injected as module
variables (_CODEBASE_JSON, _CODEBASE_JSON_LIGHT, _CODEBASE_ROOT, _LOCAL_MODE,
_LRI) rather than resolved here.
"""

import json
import os

# Set before exec via rlm_config.SETUP_CODE. Defaults below apply only to the
# legacy (non-local) path.
try:
    _CODEBASE_JSON
except NameError:
    _CODEBASE_JSON = os.environ.get("CODEBASE_JSON", "")

try:
    _CODEBASE_JSON_LIGHT
except NameError:
    _CODEBASE_JSON_LIGHT = os.environ.get(
        "CODEBASE_LIGHTWEIGHT_JSON",
        os.path.join(os.path.dirname(os.path.abspath(_CODEBASE_JSON)), "codebase_paytm_lightweight.json"),
    )

try:
    _LOCAL_MODE
except NameError:
    _LOCAL_MODE = False

try:
    _CODEBASE_ROOT
except NameError:
    _CODEBASE_ROOT = ""

try:
    _LRI
except NameError:
    _LRI = None


def load_codebase_metadata() -> dict:
    """Load the lightweight repo metadata (file tree, imports, line counts).

    In local mode the same metadata already lives in `context_0`; this is a
    convenience re-read from the on-disk lightweight JSON.
    """
    try:
        light_path = _CODEBASE_JSON_LIGHT
        with open(light_path, "r") as f:
            return json.load(f)
    except Exception as e:
        return {"error": f"Error reading metadata: {e}"}


def _load_full_index() -> dict:
    """Read the full codebase JSON from disk on demand (legacy mode only)."""
    with open(_CODEBASE_JSON, "r") as f:
        return json.load(f)


def get_file_content(path: str) -> str:
    """Read content of a specific file.

    Local mode: guarded read straight from the filesystem (returns a
    "[BLOCKED ...]" marker for sensitive/outside/binary files).
    Legacy mode: read from the full codebase JSON on disk.
    """
    if _LOCAL_MODE:
        if _LRI is None:
            return "[BLOCKED: local index reader unavailable]"
        try:
            return _LRI.read_local_file(_CODEBASE_ROOT, path)
        except Exception as e:
            return f"Error reading file: {e}"
    try:
        all_files = _load_full_index()
        file_data = all_files.get("files", {}).get(path, None)
        if file_data is None:
            return f"File not found: {path}"
        return file_data.get("content", f"No content field for: {path}")
    except Exception as e:
        return f"Error reading file: {e}"


def get_multiple_files(paths: list) -> dict:
    """Read multiple files at once; returns {path: content}."""
    if _LOCAL_MODE:
        if _LRI is None:
            return {p: "[BLOCKED: local index reader unavailable]" for p in paths}
        try:
            return _LRI.get_multiple_files_local(_CODEBASE_ROOT, paths)
        except Exception as e:
            return {p: f"Error: {e}" for p in paths}
    try:
        all_files = _load_full_index()
        files = all_files.get("files", {})
        return {p: files.get(p, {}).get("content", "Not found") for p in paths}
    except Exception as e:
        return {p: f"Error: {e}" for p in paths}


def search_files_by_keyword(keyword: str) -> list:
    """Search all file contents for a keyword and return matching paths."""
    if _LOCAL_MODE:
        if _LRI is None:
            return ["[BLOCKED: local index reader unavailable]"]
        try:
            return _LRI.search_local_files(_CODEBASE_ROOT, keyword)
        except Exception as e:
            return [f"Search error: {e}"]
    try:
        all_files = _load_full_index()
        files = all_files.get("files", {})
        return [
            path
            for path, data in files.items()
            if keyword.lower() in data.get("content", "").lower()
        ]
    except Exception as e:
        return [f"Search error: {e}"]