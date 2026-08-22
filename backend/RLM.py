from rlm import RLM
from rlm.core.types import UsageSummary
from rlm.logger import RLMLogger
from persistent_session import PersistentSession
from dotenv import load_dotenv
from testdata import eat_that_frog, codebase_content
import os
import json

load_dotenv()

ai_api_key: str = os.getenv("AI_API_KEY")

# Free models you can choose & experiment with:
# "nvidia/nemotron-3-ultra-550b-a55b:free"
# "poolside/laguna-s-2.1:free"

dill_file_size=os.stat("./rlm_sessions/user125.dill").st_size
print(f"Size of already stored RLM session {dill_file_size / 10**6} MB")

json_file_size=os.stat("./codebase_paytm.json").st_size
print(f"Size of already stored json file {json_file_size / 10**6} MB")

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
        max_depth=3,
        max_iterations=25,
        verbose=True,
        persistent=True,
    )

# ============================================================
# SESSION 1: User logs in, loads context, asks questions
# ============================================================
# print("=" * 60)
# print("SESSION 1: Loading context and asking first question")
# print("=" * 60)

# # Load anywhere
# with open("codebase_paytm.json", "r") as f:
#     CODEBASE_CONTENT = json.load(f)

# # print(CODEBASE_CONTENT)

# rlm = create_rlm()

# # First call — loads the codebase content into the REPL as context_0
# result = rlm.completion(
#     prompt=CODEBASE_CONTENT,
#     root_prompt = """
# You have access to a complete GitHub repository stored as a JSON object in the variable `context`.

# Structure of `context`:
# - context['repo_name'] — repository name
# - context['description'] — what the repo does
# - context['file_tree'] — list of all file paths
# - context['files'] — dict where each key is a file path, value contains:
#   - 'language' — programming language
#   - 'size_lines' — line count
#   - 'imports' — list of imported modules/files
#   - 'content' — full file content

# To answer any question:
# 1. First explore context['file_tree'] to find relevant files
# 2. Read specific files via context['files']['path/to/file']['content']
# 3. Follow dependency chains using context['files']['path']['imports']
# 4. Never guess — always read the actual file content before answering
# 5. Cite exact file paths in every answer

# You are a codebase expert. Answer all questions accurately based only on what exists in context.
# """
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
session.load(user_id="user125", storage_path="./rlm_sessions")

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
        "Give me a clear & proper flow chart of how different parts of this app are working & structured together."
    )
)
print("Response:", result.response)
print("Execution time:", result.execution_time, "seconds")
print("Usage:", result.usage_summary.to_dict())

# Save the session before user "logs out"
session = PersistentSession(rlm)
session_path = session.save(user_id="user125", storage_path="./rlm_sessions")
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
