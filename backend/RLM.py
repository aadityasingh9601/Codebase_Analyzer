from rlm import RLM
from colorama import Fore
from rlm.core.types import UsageSummary
from rlm.logger import RLMLogger
from persistent_session import PersistentSession
from dotenv import load_dotenv
from testdata import eat_that_frog, codebase_content
import os

load_dotenv()

ai_api_key: str = os.getenv("AI_API_KEY")

# Free models you can choose & experiment with:
# "nvidia/nemotron-3-ultra-550b-a55b:free"
# "poolside/laguna-s-2.1:free"

dill_file_size=os.stat("./rlm_sessions/user124.dill").st_size
print(f"Size of already stored RLM session {dill_file_size / 10**6} MB")

def create_rlm() -> RLM:
    """Create a fresh RLM instance with standard config."""
    return RLM(
        backend="openrouter",
        backend_kwargs={
            "model_name": "nvidia/nemotron-3-ultra-550b-a55b:free",
            "api_key": ai_api_key,
        },
        environment="local",
        environment_kwargs={
            "setup_code": "import numpy as np",
        },
        max_depth=2,
        max_iterations=10,
        verbose=True,
        persistent=True,
    )

# ============================================================
# SESSION 1: User logs in, loads context, asks questions
# ============================================================
# print("=" * 60)
# print("SESSION 1: Loading context and asking first question")
# print("=" * 60)

# rlm = create_rlm()

# # First call — loads the codebase content into the REPL as context_0
# result = rlm.completion(
#     prompt=codebase_content,
#     root_prompt="""
#     You have access to a complete codebase in the `context` variable.
#     It contains a file tree followed by all file contents with === FILE: path === delimiters.
#     You can search files using Python string operations, regex, or by reading specific sections.
#     Index the structure and be ready to answer questions about this codebase.
#     """
# )
# # result = rlm.completion(
# #     prompt=eat_that_frog,
# #     root_prompt="Load & store this book. Make it available for future queries."
# # )
# print("Response:", result.response)
# print("Execution time:", result.execution_time, "seconds")
# print("Usage:", result.usage_summary.to_dict())


print("=" * 60)
print("SESSION 2: Restoring session and continuing conversation.")
print("=" * 60)

rlm = create_rlm()

# Restore the saved state — creates the REPL environment automatically
# (no LM API call needed) and injects all saved variables + message history.
session = PersistentSession(rlm)
session.load(user_id="user124", storage_path="./rlm_sessions")

# Verify the restored state
env = rlm._persistent_env
print(f"Restored REPL variables: {[k for k in env.locals if not k.startswith('_')]}")
print(f"Context count: {env._context_count}")
print(f"History count: {env._history_count}")

# Now ask a new question — NO need to re-send the book text!
# prompt="" is fine because context_0 is already loaded in the REPL.
print("\nAsking question without re-sending context...")
result = rlm.completion(
    prompt="",
    root_prompt=(
        "Give me a flow chart of how all the files in this codebase are connected together."
    )
)
print("Response:", Fore.Yellow + result.response)
print("Execution time:", result.execution_time, "seconds")
print("Usage:", result.usage_summary.to_dict())

# Save the session before user "logs out"
session = PersistentSession(rlm)
session_path = session.save(user_id="user124", storage_path="./rlm_sessions")
print(f"\nSession saved to: {session_path}")

# Show what's in the REPL namespace before closing
env = rlm._persistent_env
print(f"REPL variables: {[k for k in env.locals if not k.startswith('_')]}")
print(f"Context count: {env._context_count}")
print(f"History count: {env._history_count}")

rlm.close()
print("\nRLM closed. User logged out.\n")


# ============================================================
# ... User logs out, closes app, comes back later ...
# ============================================================
