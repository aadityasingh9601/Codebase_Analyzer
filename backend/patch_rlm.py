"""
Patches the installed rlm library to support cross-session REPL persistence.

rlm/core/rlm.py:
- The `_skip_next_context_add` flag to RLM.__init__ and the context-skip
  check to _spawn_completion_context(), allowing PersistentSession to restore
  state without duplicating context.
- The `_persisted_root_prompt` flag and a `_merged_root_prompt()` helper so a
  ROOT/system instruction restored from a saved session is merged into the
  initial message of every completion() call — this is how the codebase
  helpers instructions (get_file_content, etc.) reach the model at query time.

rlm/utils/prompts.py:
- A deadline nudge appended to the per-turn "Turn i/N:" prompt when only 1-2
  turns remain, forcing the model to submit its best-supported answer instead
  of exhausting the turn budget unsubmitted.

Usage:
    python patch_rlm.py          # auto-detect venv and patch
    python patch_rlm.py --check  # check if already patched
    python patch_rlm.py --unpatch # revert the patch

This script is idempotent — running it multiple times is safe.
"""

import argparse
import re
import sys
from pathlib import Path


def find_rlm_rlm_py() -> Path:
    """Find rlm/core/rlm.py in the current Python environment."""
    import site

    # Check site-packages in current environment
    for sp in site.getsitepackages():
        candidate = Path(sp) / "rlm" / "core" / "rlm.py"
        if candidate.exists():
            return candidate

    # Check user site-packages
    usp = site.getusersitepackages()
    if isinstance(usp, str):
        candidate = Path(usp) / "rlm" / "core" / "rlm.py"
        if candidate.exists():
            return candidate

    # Fallback: use importlib
    try:
        import rlm.core.rlm as mod

        return Path(mod.__file__)
    except ImportError:
        pass

    raise FileNotFoundError(
        "Could not find rlm/core/rlm.py. "
        "Make sure rlm is installed in the current Python environment."
    )


# ---------- Patch content ----------

# The flag definition to insert in __init__
FLAG_LINE = (
    "        # Flag to skip adding context on the next completion() call.\n"
    "        # Used by PersistentSession.load_state() to prevent duplicating\n"
    "        # context when restoring a previously saved REPL state.\n"
    "        self._skip_next_context_add = False\n"
)

# The anchor line in __init__ right before the flag should be inserted
INIT_ANCHOR = "        self._persistent_env: SupportsPersistence | None = None"

# The flag to initialize the persisted root prompt.
ROOT_PROMPT_FLAG = (
    "        # Persisted ROOT/system instruction restored from a saved session.\n"
    "        # Merged into the initial message of each completion() by\n"
    "        # _merged_root_prompt(). Set by PersistentSession.load_state().\n"
    "        self._persisted_root_prompt = \"\"\n"
)
INIT_FLAG_ANCHOR = (
    "        # context when restoring a previously saved REPL state.\n"
    "        self._skip_next_context_add = False"
)
INIT_FLAG_LINE = "        self._persisted_root_prompt = \"\""

# The method that merges persisted + per-call root prompts, inserted right
# before RLM._setup_prompt.
ROOT_MERGE_METHOD = (
    "    def _merged_root_prompt(self, root_prompt: str | None = None) -> str | None:\n"
    "        \"\"\"Merge the persisted ROOT/system instruction with the per-call root prompt.\"\"\"\n"
    "        persisted = getattr(self, \"_persisted_root_prompt\", \"\") or \"\"\n"
    "        if not persisted:\n"
    "            return root_prompt\n"
    "        if root_prompt:\n"
    "            return f\"{persisted}\\n\\nThe user is asking: {root_prompt}\"\n"
    "        return persisted\n"
)
SETUP_ANCHOR = "    def _setup_prompt("

# In _setup_prompt, route root_prompt through the merge helper.
ROOT_PROMPT_OLD = "            root_prompt=root_prompt,"
ROOT_PROMPT_NEW = "            root_prompt=self._merged_root_prompt(root_prompt),"

# The context-skip block to replace the direct add_context call
OLD_CONTEXT_BLOCK = (
    "            environment.update_handler_address((lm_handler.host, lm_handler.port))\n"
    "            environment.add_context(prompt)"
)
NEW_CONTEXT_BLOCK = (
    "            environment.update_handler_address((lm_handler.host, lm_handler.port))\n"
    "            if not self._skip_next_context_add:\n"
    "                environment.add_context(prompt)\n"
    "            else:\n"
    "                self._skip_next_context_add = False"
)

# ---------- Deadline nudge (rlm/utils/prompts.py) ----------

# Sentinel inserted into build_user_prompt so is_patched() has a unique marker.
NUDGE_SENTINEL = "TURN BUDGET WARNING"
# The exact return statement that terminates build_user_prompt.
NUDGE_OLD = '    return {"role": "user", "content": prompt}\n'
NUDGE_NEW = (
    "    if max_iterations - iteration <= 2:\n"
    "        upcoming = max_iterations - iteration\n"
    "        prompt += (\n"
    '            f"\\n\\nTURN BUDGET WARNING - only {upcoming} turn(s) remain. "\n'
    '            "If you have an answer, submit it now: set answer[\\"content\\"] and "\n'
    '            "answer[\\"ready\\"] = True in a ```repl block. "\n'
    '            "Otherwise submit your best-supported answer based on what you have read so far."\n'
    "        )\n"
    '    return {"role": "user", "content": prompt}\n'
)


def find_rlm_prompts_py(rlm_path: Path) -> Path:
    """rlm/utils/prompts.py sits alongside rlm/core/rlm.py in the same install."""
    prompts = rlm_path.parent.parent / "utils" / "prompts.py"
    if not prompts.exists():
        raise FileNotFoundError(f"Could not find rlm/utils/prompts.py near {rlm_path}")
    return prompts


def is_prompts_patched(content: str) -> bool:
    """Check if build_user_prompt has the deadline nudge."""
    return NUDGE_SENTINEL in content


def apply_prompts_patch(content: str) -> str:
    """Insert the deadline nudge before the end of build_user_prompt."""
    if is_prompts_patched(content):
        return content
    _check_contains(content, NUDGE_OLD, "the build_user_prompt return statement")
    return content.replace(NUDGE_OLD, NUDGE_NEW)


def revert_prompts_patch(content: str) -> str:
    """Remove the deadline nudge from build_user_prompt."""
    if not is_prompts_patched(content):
        return content
    return content.replace(NUDGE_NEW, NUDGE_OLD)


def is_patched(content: str) -> bool:
    """Check if the file already has all patches applied."""
    return (
        "_skip_next_context_add" in content
        and "if not self._skip_next_context_add:" in content
        and INIT_FLAG_LINE in content
        and "self._merged_root_prompt(root_prompt)" in content
    )


def apply_patch(content: str) -> str:
    """Apply all patches to the file content."""
    # 1. Insert the _skip_next_context_add flag in __init__ after _persistent_env line
    if "_skip_next_context_add" not in content:
        if INIT_ANCHOR not in content:
            raise ValueError(
                f"Could not find anchor line in rlm.py:\n  {INIT_ANCHOR}\n"
                "The rlm library version may have changed. Manual patching required."
            )
        content = content.replace(
            INIT_ANCHOR,
            INIT_ANCHOR + "\n" + FLAG_LINE,
        )

    # 1b. Insert the _persisted_root_prompt flag after _skip_next_context_add
    if INIT_FLAG_LINE not in content:
        _check_contains(content, INIT_FLAG_ANCHOR, "the _skip_next_context_add flag")
        content = content.replace(INIT_FLAG_ANCHOR, INIT_FLAG_ANCHOR + "\n" + ROOT_PROMPT_FLAG)

    # 2. Replace the add_context block with the conditional version
    if "if not self._skip_next_context_add:" not in content:
        _check_contains(content, OLD_CONTEXT_BLOCK, "the add_context block")
        content = content.replace(OLD_CONTEXT_BLOCK, NEW_CONTEXT_BLOCK)

    # 3. Insert the _merged_root_prompt method before _setup_prompt
    if "def _merged_root_prompt" not in content:
        _check_contains(content, SETUP_ANCHOR, "the _setup_prompt method")
        content = content.replace(SETUP_ANCHOR, ROOT_MERGE_METHOD + SETUP_ANCHOR)

    # 4. Route root_prompt through the merge helper in _setup_prompt
    if ROOT_PROMPT_NEW not in content:
        _check_contains(content, ROOT_PROMPT_OLD, "the root_prompt call in _setup_prompt")
        content = content.replace(ROOT_PROMPT_OLD, ROOT_PROMPT_NEW)

    return content


def _check_contains(content: str, needle: str, label: str) -> None:
    if needle not in content:
        raise ValueError(
            f"Could not find {label} in rlm.py:\n  {needle!r}\n"
            "The rlm library version may have changed. Manual patching required."
        )


def revert_patch(content: str) -> str:
    """Revert all patches from the file content."""
    # Remove the _skip_next_context_add flag block (4 comment lines + 1 assignment line)
    flag_block_pattern = (
        r"        # Flag to skip adding context on the next completion\(\) call\.\n"
        r"        # Used by PersistentSession\.load_state\(\) to prevent duplicating\n"
        r"        # context when restoring a previously saved REPL state\.\n"
        r"        self\._skip_next_context_add = False\n"
    )
    content = re.sub(flag_block_pattern, "", content)

    # Remove the _persisted_root_prompt flag block (4 comment lines + 1 assignment line)
    root_prompt_flag_pattern = (
        r"        # Persisted ROOT/system instruction restored from a saved session\.\n"
        r"        # Merged into the initial message of each completion\(\) by\n"
        r"        # _merged_root_prompt\(\)\. Set by PersistentSession\.load_state\(\)\.\n"
        r"        self\._persisted_root_prompt = \"\"\n"
    )
    content = re.sub(root_prompt_flag_pattern, "", content)

    # Remove the _merged_root_prompt method (inserted right before _setup_prompt)
    content = re.sub(
        r"    def _merged_root_prompt\(self, root_prompt: str \| None = None\) -> str \| None:"
        r".*?\n\n    def _setup_prompt\(",
        "    def _setup_prompt(",
        content,
        flags=re.DOTALL,
    )

    # Revert the conditional block back to direct call
    content = content.replace(NEW_CONTEXT_BLOCK, OLD_CONTEXT_BLOCK)
    # Restore the direct root_prompt call
    content = content.replace(ROOT_PROMPT_NEW, ROOT_PROMPT_OLD)
    return content


def main():
    parser = argparse.ArgumentParser(description="Patch rlm for cross-session persistence")
    parser.add_argument("--check", action="store_true", help="Check if already patched")
    parser.add_argument("--unpatch", action="store_true", help="Revert the patch")
    parser.add_argument("--path", type=str, help="Explicit path to rlm/core/rlm.py")
    args = parser.parse_args()

    if args.path:
        rlm_path = Path(args.path)
        if not rlm_path.exists():
            print(f"Error: {rlm_path} not found", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            rlm_path = find_rlm_rlm_py()
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        prompts_path = find_rlm_prompts_py(rlm_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Found: {rlm_path}")
    print(f"Found: {prompts_path}")

    rlm_content = rlm_path.read_text()
    prompts_content = prompts_path.read_text()

    if args.check:
        print("rlm/core/rlm.py       :", "Already patched." if is_patched(rlm_content) else "Not patched.")
        print("rlm/utils/prompts.py  :", "Already patched." if is_prompts_patched(prompts_content) else "Not patched.")
        return

    if args.unpatch:
        if is_patched(rlm_content):
            rlm_path.write_text(revert_patch(rlm_content))
            print("rlm/core/rlm.py patch reverted successfully.")
        else:
            print("rlm/core/rlm.py: not patched — nothing to revert.")
        if is_prompts_patched(prompts_content):
            prompts_path.write_text(revert_prompts_patch(prompts_content))
            print("rlm/utils/prompts.py patch reverted successfully.")
        else:
            print("rlm/utils/prompts.py: not patched — nothing to revert.")
        return

    if is_patched(rlm_content):
        print("rlm/core/rlm.py: Already patched — nothing to do.")
    else:
        try:
            new_content = apply_patch(rlm_content)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        rlm_path.write_text(new_content)
        print("rlm/core/rlm.py patch applied successfully (persistence).")

    if is_prompts_patched(prompts_content):
        print("rlm/utils/prompts.py: Already patched — nothing to do.")
    else:
        prompts_path.write_text(apply_prompts_patch(prompts_content))
        print("rlm/utils/prompts.py patch applied successfully (deadline nudge).")


if __name__ == "__main__":
    main()
