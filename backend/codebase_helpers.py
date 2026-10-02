"""
Codebase helper functions + metadata loader for RLM.

These functions are injected into the REPL environment via the RLM's
`environment_kwargs["setup_code"]`. They are re-defined on EVERY session
(fresh and restored), so they do NOT need to be serialized into the
PersistentSession state — they always exist.

The full-codebase JSON path is set BEFORE this file is exec'd, via a
module-level line prepended in RLM.py: `_CODEBASE_JSON = <abs path>`.
Inside exec() there is no __file__, so we cannot resolve it here.
"""

import json
import os

# Where the full codebase JSON lives. Set before exec via RLM.py setup_code:
#   _CODEBASE_JSON = r"/abs/path/to/codebase_paytm.json"
# Only default (from env) if it was NOT already defined in the exec namespace.
try:
    _CODEBASE_JSON
except NameError:
    _CODEBASE_JSON = os.environ.get("CODEBASE_JSON", "")


def load_codebase_metadata() -> dict:
    """Load the lightweight repo metadata (file tree, imports, line counts)."""
    light_path = os.environ.get(
        "CODEBASE_LIGHTWEIGHT_JSON",
        os.path.join(os.path.dirname(os.path.abspath(_CODEBASE_JSON)), "codebase_paytm_lightweight.json"),
    )
    with open(light_path, "r") as f:
        return json.load(f)


def _load_full_index() -> dict:
    """Read the full codebase JSON from disk on demand."""
    with open(_CODEBASE_JSON, "r") as f:
        return json.load(f)


def get_file_content(path: str) -> str:
    """Read full content of a specific file from the codebase JSON on disk."""
    try:
        all_files = _load_full_index()
        file_data = all_files.get("files", {}).get(path, None)
        if file_data is None:
            return f"File not found: {path}"
        return file_data.get("content", f"No content field for: {path}")
    except Exception as e:
        return f"Error reading file: {e}"


def get_multiple_files(paths: list) -> dict:
    """Read multiple files at once from the codebase JSON on disk."""
    try:
        all_files = _load_full_index()
        files = all_files.get("files", {})
        return {p: files.get(p, {}).get("content", "Not found") for p in paths}
    except Exception as e:
        return {p: f"Error: {e}" for p in paths}


def search_files_by_keyword(keyword: str) -> list:
    """Search all file contents for a keyword and return matching paths."""
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
