"""
Cross-session persistence for RLM using dill serialization.

Saves/restores:
  - REPL namespace state (all variables, context data, computed values)
  - Conversation message history (so the model remembers previous turns)

How it works:
  The RLM class stores its environment as `rlm._persistent_env` (private).
  The LocalREPL inside it holds two dicts:
    - env.globals: builtins, helper functions (llm_query, SHOW_VARS, etc.)
    - env.locals: user variables (context_0, context, history_0, history,
      answer, plus any code-created variables)

  persistent=True only keeps the LocalREPL alive between completion() calls
  within the same process. It does NOT serialize to disk, and it does NOT
  carry message_history across calls (each completion() builds a fresh one).

  This module bridges that gap: it serializes env.locals (the user's variable
  namespace) and the message_history (stored in REPL via add_history() at the
  end of each completion) with dill, so state survives process restarts.

Usage:
    # --- Session 1: load context, save state ---
    rlm = RLM(..., persistent=True)
    result = rlm.completion(prompt=book_text, root_prompt="Load this book.")
    
    session = PersistentSession(rlm)
    session.save(user_id="user123")
    rlm.close()

    # --- Session 2: restore and continue ---
    rlm = RLM(..., persistent=True)
    _ = rlm.completion(prompt=".", root_prompt="Init.")  # must init env first
    
    session = PersistentSession(rlm)
    session.load(user_id="user123")
    
    # Continue without re-sending the book — context_0 is already loaded
    result = rlm.completion(prompt="", root_prompt="Count all 'e' characters.")
"""

import base64
import os
from dataclasses import dataclass, field
from typing import Any

import dill


@dataclass
class SessionState:
    """Serialized REPL state + message history for cross-session persistence.

    Attributes:
        repl_namespace: The env.locals dict — all user variables including
            context_0, context, history_0, history, and any computed variables.
        context_count: Number of contexts loaded (for proper versioning).
        history_count: Number of histories stored (for proper versioning).
        compaction_history: Trajectory segments / summaries if compaction=True.
        message_history: The full conversation trajectory from the last
            completion() — [system, metadata, user_0, assistant_0, repl_0, ...].
            This is what gets passed to the LM on each iteration, so restoring
            it means the model can continue from where it left off.
    """
    repl_namespace: dict[str, Any]
    context_count: int = 0
    history_count: int = 0
    compaction_history: list[Any] = field(default_factory=list)
    message_history: list[dict[str, Any]] = field(default_factory=list)


class PersistentSession:
    """Handles saving and loading RLM REPL state across user sessions.

    What gets saved:
        - env.locals: all variables (context_0 with the full document/book,
          any computed variables, history_0, etc.)
        - message_history: the conversation trajectory from the last
          completion() call, so the model remembers previous turns.
        - Context and history counts for proper versioning on restore.

    What is NOT saved (reconstructed on load):
        - RLM config (backend, model, etc.) — recreate the RLM instance.
        - env.globals (builtins, llm_query, SHOW_VARS, etc.) — rebuilt by
          LocalREPL.setup() when a new environment is created.
        - LM handler address — dynamically assigned per completion() call.
        - subcall_fn — a bound method reference to the RLM, reconstructed
          when the environment is re-created.

    NOTE: This class requires a small patch to rlm/core/rlm.py that adds
    `_skip_next_context_add` support. Without it, restoring state and calling
    completion() would duplicate the context as context_1.
    """

    def __init__(self, rlm_instance):
        """
        Args:
            rlm_instance: An RLM instance. persistent=True is recommended
                so the environment is reused across completion() calls.
        """
        self.rlm = rlm_instance

    def _get_repl_env(self):
        """Access the underlying LocalREPL via rlm._persistent_env.

        There is no public rlm.environment attribute. The persistent env
        is stored as the private attribute _persistent_env.
        """
        env = getattr(self.rlm, "_persistent_env", None)
        if env is None:
            raise RuntimeError(
                "No persistent environment found. "
                "Ensure RLM was created with persistent=True and at least "
                "one completion() call has been made."
            )
        return env

    def _get_message_history(self) -> list[dict[str, Any]]:
        """Retrieve the message history from the last completion() call.

        When persistent=True and a completion finishes, the RLM stores the
        full message_history into the REPL via environment.add_history() —
        so it lives in env.locals["history_0"]. We read it back from there.
        """
        env = self._get_repl_env()
        history_0 = env.locals.get("history_0")
        if history_0 is not None and isinstance(history_0, list):
            return list(history_0)
        history = env.locals.get("history")
        if history is not None and isinstance(history, list):
            return list(history)
        return []

    def save_state(self) -> SessionState:
        """Capture the current REPL state and message history.

        Call this AFTER a completion() has finished, so that:
        - The REPL namespace has all computed variables.
        - history_0 has been stored via add_history().

        Returns:
            SessionState dataclass with all serializable state.
        """
        env = self._get_repl_env()

        # Copy env.locals — contains all user variables including context_0,
        # context, history_0, history, and any code-created variables.
        repl_locals = dict(env.locals)

        # Remove 'answer' — it's a special _AnswerDict with an on_ready
        # callback reference. It gets reconstructed by setup(). Saving it
        # could pickle a stale callback reference.
        repl_locals.pop("answer", None)

        message_history = self._get_message_history()

        return SessionState(
            repl_namespace=repl_locals,
            context_count=env._context_count,
            history_count=env._history_count,
            compaction_history=list(getattr(env, "_compaction_history", [])),
            message_history=message_history,
        )

    def load_state(self, state: SessionState) -> None:
        """Restore REPL state and message history into the RLM's environment.

        After calling this, use rlm.completion() with a minimal/empty prompt
        to continue the conversation. The REPL will already have all variables,
        and the message history will be available to the model.

        Args:
            state: SessionState previously returned by save_state().
        """
        env = self._get_repl_env()

        # 1. Restore the REPL namespace (all user variables).
        #    After setup(), env.locals only has 'answer' (_AnswerDict).
        #    We inject the saved state: context_0, context, history_0,
        #    history, and all user-defined/computed variables.
        env.locals.update(state.repl_namespace)

        # Recreate aliases (env.locals.update overwrites them, but ensure
        # they point to the right objects).
        if "context_0" in env.locals:
            env.locals["context"] = env.locals["context_0"]
        if "history_0" in env.locals:
            env.locals["history"] = env.locals["history_0"]

        # Restore versioning counts so future add_context / add_history
        # calls get the correct indices (context_2, history_2, etc.)
        env._context_count = state.context_count
        env._history_count = state.history_count

        # Restore compaction history if applicable
        if state.compaction_history and hasattr(env, "_compaction_history"):
            env._compaction_history = list(state.compaction_history)
            env.locals["history"] = env._compaction_history

        # 2. Restore message history into the REPL for model reference.
        #    This lets the model access previous conversation turns via
        #    the 'history' variable in code blocks.
        if state.message_history:
            env.locals["history_0"] = list(state.message_history)
            env.locals["history"] = env.locals["history_0"]
            env._history_count = max(env._history_count, 1)

        # 3. Set the flag so the next completion() does NOT re-add the
        #    context as a new entry (which would duplicate context_0).
        #    This flag is checked in RLM._spawn_completion_context().
        self.rlm._skip_next_context_add = True

    # ----------------------------------------------------------------
    # File-based persistence (convenience methods)
    # ----------------------------------------------------------------

    def save(self, user_id: str, storage_path: str = "./rlm_sessions") -> str:
        """Save state to a .dill file on disk.

        Args:
            user_id: Unique identifier for the user/session.
            storage_path: Directory to store session files.

        Returns:
            Path to the saved file.
        """
        os.makedirs(storage_path, exist_ok=True)
        state = self.save_state()
        file_path = os.path.join(storage_path, f"{user_id}.dill")
        with open(file_path, "wb") as f:
            dill.dump(state, f)
        return file_path

    def load(self, user_id: str, storage_path: str = "./rlm_sessions") -> None:
        """Load state from a .dill file on disk.

        Args:
            user_id: Unique identifier for the user/session.
            storage_path: Directory where session files are stored.

        Raises:
            FileNotFoundError: If no saved session exists for the user.
        """
        file_path = os.path.join(storage_path, f"{user_id}.dill")
        if not os.path.exists(file_path):
            raise FileNotFoundError(
                f"No saved session found for user '{user_id}' at {file_path}"
            )
        with open(file_path, "rb") as f:
            state = dill.load(f)
        self.load_state(state)

    # ----------------------------------------------------------------
    # Database / string persistence (for storing in DB columns)
    # ----------------------------------------------------------------

    def save_to_string(self) -> str:
        """Serialize state to a base64 string (for storing in a database).

        Returns:
            Base64-encoded string of the pickled SessionState.
        """
        state = self.save_state()
        raw = dill.dumps(state)
        return base64.b64encode(raw).decode("utf-8")

    def load_from_string(self, encoded: str) -> None:
        """Restore state from a base64 string (loaded from a database).

        Args:
            encoded: Base64-encoded string from save_to_string().
        """
        raw = base64.b64decode(encoded.encode("utf-8"))
        state = dill.loads(raw)
        self.load_state(state)

    def delete(self, user_id: str, storage_path: str = "./rlm_sessions") -> bool:
        """Delete a saved session file.

        Returns:
            True if the file was deleted, False if it didn't exist.
        """
        file_path = os.path.join(storage_path, f"{user_id}.dill")
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
        return False
