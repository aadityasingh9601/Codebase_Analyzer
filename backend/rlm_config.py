import os

from dotenv import load_dotenv
from rlm import RLM

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FULL_JSON_PATH = os.path.join(SCRIPT_DIR, "codebase_paytm.json")
LIGHTWEIGHT_JSON_PATH = os.path.join(SCRIPT_DIR, "codebase_paytm_lightweight.json")
SESSION_DIR = os.path.join(SCRIPT_DIR, "rlm_sessions")

ai_api_key: str = os.getenv("AI_API_KEY")

CODEBASE_JSON_ABS = os.path.realpath(
    os.environ.get("CODEBASE_FULL_JSON", os.path.join(SCRIPT_DIR, "codebase_paytm.json"))
)
LIGHTWEIGHT_JSON_ABS = os.path.realpath(
    os.environ.get("CODEBASE_LIGHTWEIGHT_JSON", os.path.join(SCRIPT_DIR, "codebase_paytm_lightweight.json"))
)

# Local-mode repo root. Local mode is ONLY enabled when CODEBASE_ROOT is set;
# otherwise we keep the legacy full-JSON behaviour (root = this script dir,
# LOCAL_MODE=False) so nothing downstream changes.
if os.environ.get("CODEBASE_ROOT"):
    CODEBASE_ROOT_ABS = os.path.realpath(os.environ["CODEBASE_ROOT"])
    LOCAL_MODE = True
else:
    CODEBASE_ROOT_ABS = SCRIPT_DIR
    LOCAL_MODE = False


def _embed_module_source(path: str) -> str:
    """Read a module's source, stripping any `if __name__ == "__main__":` block.

    repl_guardrail.py must stay exec-embeddable: its trailing __main__ smoke
    test would otherwise run (and print) inside the RLM's setup_code.
    """
    with open(path, "r") as f:
        source = f.read()
    marker = 'if __name__ == "__main__":'
    idx = source.find(marker)
    if idx != -1:
        source = source[:idx].rstrip() + "\n"
    return source


def _build_setup_code(
    codebase_root: str,
    local_mode: bool,
    codebase_json_abs: str,
    lightweight_json_abs: str,
) -> str:
    """Build the setup_code embedded into every LocalREPL environment.

    Order matters:
      1. sys.path + absolute vars (so helper/guardrail modules import fine).
      2. Top up the sandbox __builtins__ with a few benign names the embedded
         modules reference at definition time (frozenset etc.) and import
         local_repo_index eagerly (still real __import__ pre-overlay).
      3. Embed repl_guardrail definitions + apply the overlay (in-place
         mutation of the sandbox __builtins__ dict).
      4. Embed codebase_helpers definitions last, so their `open` calls
         resolve to the jailed version at call time.

    Raw module names (os, json, ...) are only ever bound to underscore aliases
    in the preamble so the model's later `import os` always resolves to the
    safe proxy instead of a stale pre-overlay binding.
    """
    request_import = "1" if local_mode else "0"
    boot = (
        "import sys as _host_sys\n"
        "import builtins as _real_builtins\n"
        f"_host_sys.path.insert(0, {SCRIPT_DIR!r})\n"
        f"_CODEBASE_JSON = {codebase_json_abs!r}\n"
        f"_CODEBASE_JSON_LIGHT = {lightweight_json_abs!r}\n"
        f"_CODEBASE_ROOT = {codebase_root!r}\n"
        f"_LOCAL_MODE = {request_import}\n"
        "_LRI = None\n"
        "if _LOCAL_MODE:\n"
        "    import local_repo_index as _lri_mod\n"
        "    _LRI = _lri_mod\n"
    )
    topup = (
        "_need_topup = ('frozenset', 'bytes', 'bytearray', 'memoryview', "
        "'complex', 'staticmethod', 'classmethod', 'super')\n"
        "for _n in _need_topup:\n"
        "    if _n not in __builtins__:\n"
        "        __builtins__[_n] = getattr(_real_builtins, _n)\n"
    )
    return (
        boot
        + topup
        + _embed_module_source(os.path.join(SCRIPT_DIR, "repl_guardrail.py"))
        + "_apply_repl_guardrails(__builtins__, _CODEBASE_ROOT)\n"
        + open(os.path.join(SCRIPT_DIR, "codebase_helpers.py")).read()
        + "\n"
    )


SETUP_CODE = _build_setup_code(
    CODEBASE_ROOT_ABS,
    LOCAL_MODE,
    CODEBASE_JSON_ABS,
    LIGHTWEIGHT_JSON_ABS,
)

SYSTEM_PROMPT_CODEBASE = (
    "You are a Recursive Language Model (RLM) answering codebase Q&A. "
    "`context` is a dict holding repository metadata only: repo_name, description, "
    "file_tree (all file paths), files_metadata (per file: language, size_lines, imports). "
    "Actual file contents are NOT in context — fetch them on demand.\n\n"
    "You work in a persistent Python REPL. Every turn you write ONE ```repl block "
    "of real Python code; plan in prose first, then code, one block per turn.\n"
    "- Only print() output (stdout) is shown back to you between turns. Wrap everything you want to see in print(...). Do not rely on bare expressions.\n"
    "- The REPL is NOT a Jupyter cell: no markdown, no pseudo-tool tags. There is NO server-side tool registry — "
    "every helper is a plain Python function you must call as code. Never emit <tool_call> XML blocks or "
    "function-call skeletons; ONLY code inside ```repl blocks is executed, and only its stdout is returned.\n"
    "- Stdout is truncated (about 20K chars) and re-sent on every later turn — keep print() calls tiny. "
    "Never print a whole large file wholesale: print counts or the first few hundred chars, or pass big "
    "content through llm_query and print only its terse summary. Long stdouts bloat history and cost tokens.\n"
    "Available functions (ordinary Python, call them directly, e.g. print(get_file_content(path))):\n"
    "- get_file_content(path) -> str\n"
    "- get_multiple_files([path1, path2, ...]) -> dict\n"
    "- search_files_by_keyword(keyword) -> list of paths\n\n"
    "Delegation (sub-LLMs) — always available, and REQUIRED for large work instead of solving inline:\n"
    "- llm_query(prompt) -> str: single fast LLM call. Use for one-shot extraction or summarization over a chunk of text.\n"
    "- llm_query_batched([prompts]) -> list: many independent llm_query calls run concurrently.\n"
    "- rlm_query(prompt) -> str: spawn a child RLM with its own REPL and iteration for subtasks needing multi-step reasoning. Falls back to llm_query if recursion is unavailable.\n"
    "- rlm_query_batched([prompts]) -> list: parallel recursive children.\n"
    "Use them to summarize a huge file, synthesize across many files, or decompose a big question into sub-problems "
    "and combine the answers in code (plan steps, branch on results, aggregate). For small direct lookups, read files directly instead.\n\n"
    "Submitting: the REPL exposes `answer` (a dict with \"content\" and \"ready\" keys). When done, run one repl block:\n"
    "  answer[\"content\"] = \"final answer\"\n"
    "  answer[\"ready\"] = True\n"
    "The run ends as soon as ready is True. Copy exactly; no trailing commas; never flip ready before you have the answer. "
    "If turns are nearly exhausted, submit your best-supported answer rather than exhausting unsubmitted."
)


def create_rlm(
    logger=None,
    model_name: str = "inclusionai/ling-3.0-flash-fin:free",
    sys_prompt: str = "distilled",
    repo_root: str | None = None,
) -> RLM:
    if repo_root is not None:
        setup_code = _build_setup_code(
            os.path.realpath(repo_root),
            True,
            CODEBASE_JSON_ABS,
            LIGHTWEIGHT_JSON_ABS,
        )
    else:
        setup_code = SETUP_CODE
    if sys_prompt == "distilled":
        custom_system_prompt, orchestrator = SYSTEM_PROMPT_CODEBASE, False
    else:
        custom_system_prompt, orchestrator = None, True
    return RLM(
        backend="openrouter",
        backend_kwargs={
            "model_name": model_name,
            "api_key": ai_api_key,
        },
        environment="local",
        environment_kwargs={
            "setup_code": setup_code,
        },
        logger=logger,
        max_depth=2,
        max_iterations=8,
        max_errors=3,
        max_timeout=240,
        max_tokens=200_000,
        sampling_args={"temperature": 0.2},
        custom_system_prompt=custom_system_prompt,
        orchestrator=orchestrator,
        verbose=True,
        persistent=True,
    )