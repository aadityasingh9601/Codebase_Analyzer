import argparse
import json
import os
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
    "What does apps/user-app/app/lib/auth.ts do?",
]


def write_results(results: list[dict], questions: list[str]) -> None:
    payload = {
        "run_metadata": {
            "generated_at": datetime.now().isoformat(),
            "model": results[0]["model"] if results else None,
            "total_queries": len(questions),
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
    return metrics


def print_metrics(metrics: dict, mode: str) -> None:
    print("-" * 70)
    if mode == "rlm":
        print(
            f"Iterations: {metrics['iterations']} | "
            f"Time: {metrics['execution_time_seconds']:.2f}s | "
            f"Input tokens: {metrics['input_tokens']} | "
            f"Output tokens: {metrics['output_tokens']}"
        )
    else:
        print(
            f"Tier: {metrics['tier']} | "
            f"Source: {metrics['answer_source']} | "
            f"Time: {metrics['execution_time_seconds']:.2f}s"
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


def run_benchmark(mode: str) -> None:
    rlm = create_rlm(logger=RLMLogger())
    session = PersistentSession(rlm)
    session.load(user_id="user126_paytm", storage_path=SESSION_DIR)

    results: list[dict] = []

    try:
        for index, question in enumerate(QUESTIONS, start=1):
            print("=" * 70)
            print(f"[{index}/{len(QUESTIONS)}] Question: {question}")
            print("=" * 70)

            try:
                if mode == "rlm":
                    result = rlm.completion(prompt="", root_prompt=question)
                    response = result.response
                    model = result.root_model
                    metrics = flatten_metrics(result, mode)
                else:
                    result = route_and_answer(question, rlm=rlm)
                    response = result.answer
                    model = DEFAULT_MODEL
                    metrics = flatten_metrics(result, mode)
                    if result.error:
                        print(Fore.RED + f"Router error: {result.error}")
            except Exception as exc:
                print(Fore.RED + f"{mode} call failed: {exc}")
                entry = {
                    "question": question,
                    "response": None,
                    "comment": None,
                    "error": str(exc),
                    "metrics": None,
                }
                results.append(entry)
                write_results(results, QUESTIONS)
                continue

            print(Fore.LIGHTYELLOW_EX + str(response))
            print_metrics(metrics, mode)
            comment = prompt_comment()

            entry = {
                "question": question,
                "response": response,
                "comment": comment,
                "model": model,
                "metrics": metrics,
                "completed_at": datetime.now().isoformat(),
            }
            results.append(entry)
            write_results(results, QUESTIONS)
    finally:
        rlm.close()

    print("=" * 70)
    print(f"Benchmark complete ({mode} mode). Results saved to: {RESULTS_FILE}")
    print("=" * 70)


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
    args = parser.parse_args()
    run_benchmark(args.mode)