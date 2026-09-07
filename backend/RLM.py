from rlm import RLM
from persistent_session import PersistentSession
from dotenv import load_dotenv
import os
from prompt import ROOT_PROMPT
import json
from colorama import Fore, Back, Style

load_dotenv()

ai_api_key: str = os.getenv("AI_API_KEY")

# Resolve paths relative to this script's directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FULL_JSON_PATH = os.path.join(SCRIPT_DIR, "codebase_paytm.json")
LIGHTWEIGHT_JSON_PATH = os.path.join(SCRIPT_DIR, "codebase_paytm_lightweight.json")
SESSION_DIR = os.path.join(SCRIPT_DIR, "rlm_sessions")

# The helper functions (get_file_content, search_files_by_keyword, ...) live
# in codebase_helpers.py. We inject them into the REPL via setup_code, so they
# are re-defined on EVERY session (fresh and restored) — no need to persist them.
# We prepend an absolute path for the full-codebase JSON because the REPL runs
# code under a temp cwd and there is no __file__ inside exec().
_CODEBASE_JSON_ABS = os.path.join(SCRIPT_DIR, "codebase_paytm.json")
SETUP_CODE = (
    f'_CODEBASE_JSON = r"{_CODEBASE_JSON_ABS}"\n'
    + open(os.path.join(SCRIPT_DIR, "codebase_helpers.py")).read()
    + "\n"
)

dill_file_size=os.stat("./rlm_sessions/user126_paytm.dill").st_size
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
        max_depth=2,
        max_iterations=25,
        verbose=True,
        persistent=True,
    )




with open(LIGHTWEIGHT_JSON_PATH, "r") as f:
    lightweight_data = json.load(f)

print(f"Files in repo: {len(lightweight_data['file_tree'])}")

# ============================================================
# SESSION 1: Load codebase metadata + define on-demand functions
# ============================================================
# print("=" * 60)
# print("SESSION 1: Loading codebase metadata")
# print("=" * 60)

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

# ============================================================
# SESSION 2: Restore and ask questions (uncomment when ready)
# ============================================================
print("=" * 60)
print("SESSION 2: Restoring session and continuing conversation.")
print("=" * 60)

rlm = create_rlm()

session = PersistentSession(rlm)
session.load(user_id="user126_paytm", storage_path=SESSION_DIR)

env = rlm._persistent_env
print(f"context_0 type: {type(env.locals.get('context_0')).__name__}")
print(f"Functions available: {[k for k in env.locals if callable(env.locals[k])]}")

result = rlm.completion(
    prompt="",
    root_prompt="Give me a architecture diagram of the project.",
)
print("Response:", Fore.LIGHTYELLOW_EX + str(result.response))
print("Execution time:", result.execution_time, "seconds")

# # Save session
# session = PersistentSession(rlm)
# session_path = session.save(user_id="user126_paytm", storage_path="./rlm_sessions")
# print(f"\nSession saved to: {session_path}")

print(f"REPL variables: {[k for k in env.locals if not k.startswith('_')]}")

rlm.close()
