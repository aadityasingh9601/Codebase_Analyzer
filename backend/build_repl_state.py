"""
Build an RLM REPL state (".dill") directly from the stored codebase JSON files,
WITHOUT running an RLM query or making any LLM call.

Why this exists:
  - Normally the initial RLM state is created by sending the first "understand
    the codebase" query (root_prompt + codebase metadata) through the RLM, which
    runs an expensive/LLM-backed Session 1, then saves the REPL state.
  - When the remote repo updates, the .json files are regenerated, but the
    .dill had to be deleted and rebuilt by re-running that query.
  - This module removes that hassle: it crafts the same initial REPL state the
    RLM would have produced (metadata loaded into `context_0`), serializes it to
    a .dill file, and saves it. When a user later asks a query, that .dill is
    loaded first (via PersistentSession.load), so the RLM REPL already has the
    codebase context without ever needing the initial query.

Resulting state:
  - repl_namespace: {context_0: <lightweight metadata dict>, context: <same>}
  - context_count = 1  (one codebase context loaded)
  - history_count = 0  (no prior conversations — each query starts fresh)
  - message_history = [] (no prior trajectory)

Usage:
    python build_repl_state.py                                # build default repo
    python build_repl_state.py --light-json <path>            # explicit light json
    python build_repl_state.py --root-prompt "<prompt>"       # custom root prompt
    python build_repl_state.py --user <user_id>               # dill filename
    python build_repl_state.py --storage <dir>                # dill output dir

    # Or import:
    from build_repl_state import build_repl_state_from_json
    path = build_repl_state_from_json(
        lightweight_json="codebase_paytm_lightweight.json",
        root_prompt=ROOT_PROMPT,
        user_id="user126_paytm",
        storage_path="./rlm_sessions",
    )
"""

import argparse
import json
import os
from prompt import ROOT_PROMPT

from persistent_session import SessionState

# Defaults matching the existing local indices (override via argv).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LIGHT_JSON = os.path.join(SCRIPT_DIR, "codebase_paytm_lightweight.json")
DEFAULT_STORAGE_PATH = os.path.join(SCRIPT_DIR, "rlm_sessions")
DEFAULT_USER_ID = "user126_paytm"
DEFAULT_ROOT_PROMPT = ROOT_PROMPT


def build_repl_state_from_json(
    lightweight_json: str,
    root_prompt: str = "",
    user_id: str = DEFAULT_USER_ID,
    storage_path: str = DEFAULT_STORAGE_PATH,
) -> str:
    """Build a .dill REPL state from the lightweight codebase JSON, no LLM call.

    Args:
        lightweight_json: Path to the lightweight codebase JSON (metadata only,
            i.e. what the RLM stores in `context_0`).
        root_prompt: The REBOOT/system instruction for the codebase. It is
            embedded only so the state is self-describing if later referenced;
            it does NOT trigger any LLM call here.
        user_id: The session id used for the .dill file name (<user_id>.dill).
        storage_path: Directory where the .dill file is written.

    Returns:
        The absolute path to the written .dill file.
    """
    with open(lightweight_json, "r") as f:
        lightweight_data = json.load(f)

    # The metadata dict is exactly what the RLM stores as `context_0`. We place
    # it in `context_0` (the canonical name) and alias `context` to it so the
    # model's code blocks can do `context['file_tree']` etc. right away.
    context_0 = lightweight_data
    repl_namespace = {
        "context_0": context_0,
        "context": context_0,
    }

    state = SessionState(
        repl_namespace=repl_namespace,
        context_count=1,      # one codebase context loaded
        history_count=0,      # no prior conversations
        compaction_history=[],
        message_history=[],   # no prior trajectory
    )

    os.makedirs(storage_path, exist_ok=True)
    file_path = os.path.join(storage_path, f"{user_id}.dill")

    # Defer import so this module itself stays side-effect-free at import time.
    import dill

    with open(file_path, "wb") as f:
        dill.dump(state, f)

    return os.path.abspath(file_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--light-json",
        default=DEFAULT_LIGHT_JSON,
        help="Path to the lightweight codebase JSON (metadata).",
    )
    parser.add_argument(
        "--root-prompt",
        default="",
        help="Optional root/system instruction embedded in the state.",
    )
    parser.add_argument("--user", default=DEFAULT_USER_ID, help="Session id (dill filename).")
    parser.add_argument("--storage", default=DEFAULT_STORAGE_PATH, help="Output directory for the .dill.")
    args = parser.parse_args()

    path = build_repl_state_from_json(
        lightweight_json=args.light_json,
        root_prompt=args.root_prompt,
        user_id=args.user,
        storage_path=args.storage,
    )
    print(f"Built REPL state -> {path}")


if __name__ == "__main__":
    main()
