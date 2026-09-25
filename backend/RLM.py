from persistent_session import PersistentSession
from dotenv import load_dotenv
import os
import sys
from prompt import ROOT_PROMPT
import json
from colorama import Fore, Back, Style
from rlm_config import (
    create_rlm,
    SCRIPT_DIR,
    FULL_JSON_PATH,
    LIGHTWEIGHT_JSON_PATH,
    SESSION_DIR,
)
from query_router import DEFAULT_MODEL, route_and_answer

load_dotenv()

dill_file_size=os.stat("./rlm_sessions/user131_cal.diy.dill").st_size
print(f"Size of already stored RLM session {dill_file_size / 10**6} MB")

json_file_size=os.stat(FULL_JSON_PATH).st_size
print(f"Size of FULL json file {json_file_size / 10**6} MB")

json_file_size=os.stat(LIGHTWEIGHT_JSON_PATH).st_size
print(f"Size of LIGHTWEIGHT json file {json_file_size / 10**6} MB")

# You can also try with these LLM models. Choose a model with low latency.
# nvidia/nemotron-3-ultra-550b-a55b:free (Very high latency)
# minimax/minimax-m3:free (Not free anymore)
# poolside/laguna-s-2.1:free
# nvidia/nemotron-3.5-lightning:free
# thinkingmachines/inkling:free (Can't use here, only for agentic harnesses)
# inclusionai/ling-3.0-flash-fin:free


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

# # Save session
# session = PersistentSession(rlm)
# session_path = session.save(user_id="user126_paytm", storage_path="./rlm_sessions")
# print(f"\nSession saved to: {session_path}")

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
session.load(user_id="user131_cal.diy", storage_path=SESSION_DIR)

env = rlm._persistent_env
print(f"context_0 type: {type(env.locals.get('context_0')).__name__}")
print(f"Functions available: {[k for k in env.locals if callable(env.locals[k])]}")

question = " ".join(sys.argv[1:]) or "List down the contents of all the .css files in this repo."

try:
    result = rlm.completion(
        prompt="",
        root_prompt=question,
    )
    print("Response:", Fore.LIGHTYELLOW_EX + str(result.response))
    print("Execution time:", result.execution_time, "seconds")
except Exception as exc:
    print(Fore.RED + f"\nRLM call failed: {exc}")
finally:
    print(f"REPL variables: {[k for k in env.locals if not k.startswith('_')]}")
    rlm.close()
