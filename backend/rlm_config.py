
import os

from dotenv import load_dotenv
from rlm import RLM

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FULL_JSON_PATH = os.path.join(SCRIPT_DIR, "codebases/codebase_cal.diy.json")
LIGHTWEIGHT_JSON_PATH = os.path.join(SCRIPT_DIR, "codebases/codebase_cal.diy_lightweight.json")
SESSION_DIR = os.path.join(SCRIPT_DIR, "rlm_sessions")

ai_api_key: str = os.getenv("AI_API_KEY")

CODEBASE_JSON_ABS = os.path.join(SCRIPT_DIR, "codebases/codebase_cal.diy.json")
LIGHTWEIGHT_JSON_ABS = os.path.join(SCRIPT_DIR, "codebases/codebase_cal.diy_lightweight.json")

SETUP_CODE = (
    f'_CODEBASE_JSON = r"{CODEBASE_JSON_ABS}"\n'
    f'_LIGHTWEIGHT_JSON = r"{LIGHTWEIGHT_JSON_ABS}"\n'
    f'_CODEBASE_SESSION_ID = r"{os.getenv("CODEBASE_SESSION_ID", "repl")}"\n'
    + open(os.path.join(SCRIPT_DIR, "codebase_helpers.py")).read()
    + "\n"
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
) -> RLM:
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
            "setup_code": SETUP_CODE,
        },
        logger=logger,
        max_depth=2,
        max_iterations=30,
        max_errors=3,
        max_timeout=240,
        max_tokens=1000000,
        sampling_args={"temperature": 0.2},
        custom_system_prompt=custom_system_prompt,
        orchestrator=orchestrator,
        verbose=True,
        persistent=True,
    )