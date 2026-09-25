import argparse
import json
import os
import re
import time
from datetime import datetime

from colorama import Fore
from dotenv import load_dotenv

from persistent_session import PersistentSession
from query_router import DEFAULT_MODEL, route_and_answer
from rlm.logger import RLMLogger
from rlm_config import SCRIPT_DIR, SESSION_DIR, create_rlm

load_dotenv()

RESULTS_FILE = os.path.join(
    SCRIPT_DIR,
    f"rlm_benchmark_results_{datetime.now():%Y%m%d_%H%M%S}.json",
)

# Add your evaluation questions here.
QUESTIONS = [
    "How many files are there in this repo?",
    # "What's the tech stack of this application?",
    # "Print the file contents of apps/user-app/components/AccountForm.tsx.",
    # "Print the file contents of apps/bank-webhook/src/index.ts.",
    #  "Find all places where authentication is handled. List exact file paths and line-level descriptions.",
    #  "Give me a flow chart of how it’s working.",
    #  "Give me a high level overview of this codebase — what does it do, how is it structured, what are the main components?",
    #  "What is the entry point of this application and what happens when it starts up?",
    #  "What design patterns are used in this codebase? Give examples with file references.",
    #  "If I wanted to add a new API endpoint, which files would I need to modify and in what order?",
    #  "Are there any circular dependencies in this codebase?",
    #  "What environment variables does this project require and where are they used?",
    #  "How can someone setup this repo locally if they want to contribute to it?",
    #  "Explain a bug that could exist in the error handling across this codebase.",
    #  "Compare how authentication is handled in this project vs best practices — what's missing?",
    #  "Draw me the dependency graph between the main modules — which files depend on which?” 
    # "Which file provides the bank webhook endpoint, and what does it import?",
    # "Explain how tests are working in this repo.",
    # "Summarize the overall backend architecture: read the main entry points of each service and explain how the services communicate with each other.",
]

# Heuristic for detecting answers where the model never actually read the file.
_COMPLAINT_RE = re.compile(
    r"\bcontext is empty\b"
    r"|(i can't|i cannot|i could n?ot|i am unable to)\b"
    r".{0,80}(access|see|view|read|retrieve).{0,80}(the )?(actual )?file (contents?|content)\b"
    r'|\b(no|don\'?t have) .{0,40}(file )?(contents?|content)\b',
    re.IGNORECASE | re.DOTALL,
)

_TOOL_SKELETON_RE = re.compile(
    r"<tool_call>|<arg_key>|<arg_value>",
    re.IGNORECASE,
)


def _question_succeeded(response, error) -> bool:
    """True when a usable answer (not a complaint / crash) came back."""
    if error:
        return False
    if not response or not str(response).strip():
        return False
    if str(response).strip().lower() == "none":
        return False
    if _COMPLAINT_RE.search(str(response)):
        return False
    if _TOOL_SKELETON_RE.search(str(response)):
        return False
    return True


def write_results(results: list[dict], questions: list[str], sys_prompt: str) -> None:
    payload = {
        "run_metadata": {
            "generated_at": datetime.now().isoformat(),
            "model": results[0].get("model") if results else None,
            "total_queries": len(questions),
            "system_prompt": sys_prompt,
        },
        "results": results,
    }
    with open(RESULTS_FILE, "w") as f:
        json.dump(payload, f, indent=2, default=str)


def prompt_comment() -> str:
    try:
        return input("Enter your comment on this response: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def flatten_metrics(
    result,
    mode: str,
    max_iterations: int | None = None,
) -> dict:
    metrics = {
        "execution_time_seconds": (
            result.execution_time if mode == "rlm" else result.elapsed_seconds
        ),
    }
    if mode == "rlm":
        iterations = 0
        if result.metadata:
            iterations = len(result.metadata.get("iterations", []))
        metrics["iterations"] = iterations
        metrics["input_tokens"] = result.usage_summary.total_input_tokens
        metrics["output_tokens"] = result.usage_summary.total_output_tokens
        metrics["exhausted"] = (
            max_iterations is not None and iterations >= max_iterations
        )
    else:
        metrics["tier"] = result.tier
        metrics["reason"] = result.reason
        metrics["answer_source"] = result.answer_source
        metrics["evidence_paths"] = result.evidence_paths
        metrics["direct_calls"] = result.direct_calls
        metrics["direct_input_tokens"] = result.direct_input_tokens
        metrics["direct_output_tokens"] = result.direct_output_tokens
        metrics["input_tokens"] = (
            result.direct_input_tokens + result.rlm_input_tokens
        )
        metrics["output_tokens"] = (
            result.direct_output_tokens + result.rlm_output_tokens
        )
        metrics["rlm_input_tokens"] = result.rlm_input_tokens
        metrics["rlm_output_tokens"] = result.rlm_output_tokens
        metrics["rlm_iterations"] = result.rlm_iterations
        metrics["exhausted"] = (
            result.rlm_iterations is not None
            and max_iterations is not None
            and result.rlm_iterations >= max_iterations
        )
    return metrics


def print_metrics(metrics: dict, mode: str) -> None:
    print("-" * 70)
    if mode == "rlm":
        print(
            f"Iterations: {metrics['iterations']} | "
            f"Time: {metrics['execution_time_seconds']:.2f}s | "
            f"Input tokens: {metrics['input_tokens']} | "
            f"Output tokens: {metrics['output_tokens']} | "
            f"Exhausted: {metrics['exhausted']}"
        )
    else:
        print(
            f"Tier: {metrics['tier']} | "
            f"Source: {metrics['answer_source']} | "
            f"Time: {metrics['execution_time_seconds']:.2f}s | "
            f"Exhausted: {metrics['exhausted']}"
        )
        print(
            f"Direct calls: {metrics['direct_calls']} | "
            f"Direct in/out: {metrics['direct_input_tokens']}/{metrics['direct_output_tokens']}"
        )
        if metrics.get("rlm_iterations"):
            print(
                f"RLM in/out: {metrics['rlm_input_tokens']}/{metrics['rlm_output_tokens']} | "
                f"RLM iterations: {metrics['rlm_iterations']}"
            )


def run_benchmark(
    mode: str,
    questions: list[str] | None = None,
    sys_prompt: str = "distilled",
    pace: int = 0,
) -> None:
    rlm = create_rlm(logger=RLMLogger(), sys_prompt=sys_prompt)
    session = PersistentSession(rlm)
    session.load(user_id="user131_cal.diy", storage_path=SESSION_DIR)
    max_iterations = rlm.max_iterations

    questions = questions or QUESTIONS
    results: list[dict] = []

    try:
        for index, question in enumerate(questions, start=1):
            if index > 1 and pace:
                time.sleep(pace)
            print("=" * 70)
            print(f"[{index}/{len(questions)}] Question: {question}")
            print("=" * 70)

            try:
                if mode == "rlm":
                    result = rlm.completion(prompt="", root_prompt=question)
                    response = result.response
                    model = result.root_model
                    metrics = flatten_metrics(result, mode, max_iterations=max_iterations)
                else:
                    result = route_and_answer(question, rlm=rlm)
                    response = result.answer
                    model = DEFAULT_MODEL
                    metrics = flatten_metrics(result, mode, max_iterations=max_iterations)
                    if result.error:
                        print(Fore.RED + f"Router error: {result.error}")
            except Exception as exc:
                print(Fore.RED + f"{mode} call failed: {exc}")
                entry = {
                    "question": question,
                    "response": None,
                    "comment": None,
                    "error": str(exc),
                    "success": False,
                    "metrics": None,
                    "completed_at": datetime.now().isoformat(),
                }
                results.append(entry)
                write_results(results, questions, sys_prompt)
                continue

            success = _question_succeeded(response, None)
            metrics["success"] = success
            print(Fore.LIGHTYELLOW_EX + str(response))
            print_metrics(metrics, mode)
            print(f"Success: {success}")
            comment = prompt_comment()

            entry = {
                "question": question,
                "response": response,
                "comment": comment,
                "model": model,
                "success": success,
                "metrics": metrics,
                "completed_at": datetime.now().isoformat(),
            }
            results.append(entry)
            write_results(results, questions, sys_prompt)
    finally:
        rlm.close()

    _print_summary(results, mode)
    print("=" * 70)
    print(f"Benchmark complete ({mode} mode). Results saved to: {RESULTS_FILE}")
    print("=" * 70)


def _print_summary(results: list[dict], mode: str) -> None:
    valid = [r for r in results if r.get("metrics")]
    if not valid:
        return
    n = len(valid)
    successes = sum(1 for r in valid if r.get("success"))
    avg_iters = sum(r["metrics"].get("iterations") or 0 for r in valid) / n
    avg_in = sum(r["metrics"].get("input_tokens") or 0 for r in valid) / n
    avg_out = sum(r["metrics"].get("output_tokens") or 0 for r in valid) / n
    avg_time = sum(r["metrics"].get("execution_time_seconds") or 0 for r in valid) / n
    exhausted = sum(1 for r in valid if r["metrics"].get("exhausted"))
    print("=" * 70)
    print(f"Summary ({mode} mode, {n} questions):")
    print(f"  Success rate : {successes}/{n} ({100 * successes / n:.0f}%)")
    print(f"  Avg iterations : {avg_iters:.1f} | Exhausted: {exhausted}/{n}")
    print(f"  Avg input tokens  : {avg_in:,.0f}")
    print(f"  Avg output tokens : {avg_out:,.0f}")
    print(f"  Avg time          : {avg_time:.1f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Benchmark question answering against the codebase."
    )
    parser.add_argument(
        "--mode",
        choices=["rlm", "router"],
        default="rlm",
        help="'rlm' runs the full RLM; 'router' runs the tiered router.",
    )
    parser.add_argument(
        "--questions",
        nargs="+",
        help="Override the QUESTIONS list with one or more questions.",
    )
    parser.add_argument(
        "--sys-prompt",
        choices=["default", "distilled"],
        default="distilled",
        help="Which system prompt to run: 'default' (upstream + orchestrator addendum) or 'distilled' (codebase prompt).",
    )
    parser.add_argument(
        "--pace",
        type=int,
        default=0,
        help="Sleep this many seconds between questions (free-tier rate limiting).",
    )
    args = parser.parse_args()
    run_benchmark(
        args.mode,
        questions=args.questions,
        sys_prompt=args.sys_prompt,
        pace=args.pace,
    )