from rlm import RLM
from persistent_session import PersistentSession
from dotenv import load_dotenv
import os
import json

load_dotenv()

ai_api_key: str = os.getenv("AI_API_KEY")

# Resolve paths relative to this script's directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FULL_JSON_PATH = os.path.join(SCRIPT_DIR, "codebase_adobe.json")
LIGHTWEIGHT_JSON_PATH = os.path.join(SCRIPT_DIR, "codebase_adobe_lightweight.json")
SESSION_DIR = os.path.join(SCRIPT_DIR, "rlm_sessions")

# The helper functions (get_file_content, search_files_by_keyword, ...) live
# in codebase_helpers.py. We inject them into the REPL via setup_code, so they
# are re-defined on EVERY session (fresh and restored) — no need to persist them.
# We prepend an absolute path for the full-codebase JSON because the REPL runs
# code under a temp cwd and there is no __file__ inside exec().
_CODEBASE_JSON_ABS = os.path.join(SCRIPT_DIR, "codebase_adobe.json")
SETUP_CODE = (
    f'_CODEBASE_JSON = r"{_CODEBASE_JSON_ABS}"\n'
    + open(os.path.join(SCRIPT_DIR, "codebase_helpers.py")).read()
    + "\n"
)

dill_file_size=os.stat("./rlm_sessions/user124.dill").st_size
print(f"Size of already stored RLM session {dill_file_size / 10**6} MB")

json_file_size=os.stat(FULL_JSON_PATH).st_size
print(f"Size of FULL json file {json_file_size / 10**6} MB")

json_file_size=os.stat(LIGHTWEIGHT_JSON_PATH).st_size
print(f"Size of LIGHTWEIGHT json file {json_file_size / 10**6} MB")

# You can also try with these LLM models.
# minimax/minimax-m3:free           nvidia/nemotron-3-ultra-550b-a55b:free
# poolside/laguna-s-2.1:free
# nvidia/nemotron-3.5-lightning:free
# thinkingmachines/inkling:free

def create_rlm() -> RLM:
    """Create a fresh RLM instance with standard config."""
    return RLM(
        backend="openrouter",
        backend_kwargs={
            "model_name": "minimax/minimax-m3:free",
            "api_key": ai_api_key,
        },
        environment="local",
        environment_kwargs={
            "setup_code": SETUP_CODE,
        },
        max_depth=3,
        max_iterations=25,
        verbose=True,
        persistent=True,
    )


ROOT_PROMPT = """
You have access to a GitHub repository. The variable `context` holds metadata only.

CONTEXT STRUCTURE (context is a dict):
- context['repo_name'] — repository name
- context['description'] — what the repo does
- context['file_tree'] — list of all file paths
- context['files_metadata'][path] — metadata per file:
  - 'language' — programming language
  - 'size_lines' — line count
  - 'imports' — list of imported modules/files

AVAILABLE FUNCTIONS (read actual code from disk on demand):
- get_file_content(path) → returns full content of one file
- get_multiple_files([path1, path2]) → returns dict of multiple files
- search_files_by_keyword(keyword) → returns list of paths containing keyword

STRICT WORKFLOW — follow this exact order:
1. Explore context['file_tree'] to identify relevant files
2. Use search_files_by_keyword(keyword) for broad searches
3. Read specific files using get_file_content(path) — NEVER guess contents
4. Follow dependency chains via context['files_metadata'][path]['imports']
5. Cite exact file paths in every answer

You are a codebase expert. Answer accurately based only on what you read.
Never stop early — keep reading files until you have enough info to answer completely.
Understand the provided codebase properly & get ready for answering questions on it.
"""


# ============================================================
# SESSION 1: Load codebase metadata + define on-demand functions
# ============================================================
# print("=" * 60)
# print("SESSION 1: Loading codebase metadata")
# print("=" * 60)

with open(LIGHTWEIGHT_JSON_PATH, "r") as f:
    lightweight_data = json.load(f)

print(f"Files in repo: {len(lightweight_data['file_tree'])}")

# IMPORTANT: pass the metadata as a DICT (not a code string).
# LocalREPL.add_context stores dict payloads as proper context_0 dicts,
# so the model can do context['file_tree'] directly. If we embedded it in a
# giant code string and passed that as the prompt, context_0 would become a
# raw string and the model would trip over json.load errors.
# rlm = create_rlm()

# result = rlm.completion(
#     prompt=lightweight_data,   # <-- the dict, not a f-string code blob
#     root_prompt=ROOT_PROMPT,
# )

# print("Response:", result.response)
# print("Execution time:", result.execution_time, "seconds")
# print("Usage:", result.usage_summary.to_dict())

# # Sanity-check what the REPL actually holds
# env = rlm._persistent_env
# print(f"context_0 type: {type(env.locals.get('context_0')).__name__}")
# print(f"Functions available: {[k for k in env.locals if callable(env.locals[k])]}")

# rlm.close()
# print("\nRLM closed. User logged out.\n")


# ============================================================
# SESSION 2: Restore and ask questions (uncomment when ready)
# ============================================================
print("=" * 60)
print("SESSION 2: Restoring session and continuing conversation.")
print("=" * 60)

rlm = create_rlm()

session = PersistentSession(rlm)
session.load(user_id="user124", storage_path=SESSION_DIR)

env = rlm._persistent_env
print(f"context_0 type: {type(env.locals.get('context_0')).__name__}")
print(f"Functions available: {[k for k in env.locals if callable(env.locals[k])]}")

result = rlm.completion(
    prompt="",
    root_prompt="Give me a clear flow chart of how the app is structured.",
)
print("Response:", result.response)
print("Execution time:", result.execution_time, "seconds")

# Save session
session = PersistentSession(rlm)
session_path = session.save(user_id="user124", storage_path="./rlm_sessions")
print(f"\nSession saved to: {session_path}")

print(f"REPL variables: {[k for k in env.locals if not k.startswith('_')]}")

rlm.close()
