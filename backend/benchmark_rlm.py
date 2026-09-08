import json
import os
from datetime import datetime

from colorama import Fore
from dotenv import load_dotenv

from persistent_session import PersistentSession
from rlm import RLM
from rlm.logger import RLMLogger

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_DIR = os.path.join(SCRIPT_DIR, "rlm_sessions")
RESULTS_FILE = os.path.join(
    SCRIPT_DIR,
    f"rlm_benchmark_results_{datetime.now():%Y%m%d_%H%M%S}.json",
)

ai_api_key: str = os.getenv("AI_API_KEY")

# Add your evaluation questions here.
QUESTIONS = [
    "How many files are there in the repository?",
    "What's the tech stack of this repository?"
]

_CODEBASE_JSON_ABS = os.path.join(SCRIPT_DIR, "codebase_paytm.json")
SETUP_CODE = (
    f'_CODEBASE_JSON = r"{_CODEBASE_JSON_ABS}"\n'
    + open(os.path.join(SCRIPT_DIR, "codebase_helpers.py")).read()
    + "\n"
)


def create_rlm() -> RLM:
    return RLM(
        backend="openrouter",
        backend_kwargs={
            "model_name": "inclusionai/ling-3.0-flash-fin:free",
            "api_key": ai_api_key,
        },
        environment="local",
        environment_kwargs={
            "setup_code": SETUP_CODE,
        },
        logger=RLMLogger(),
        max_depth=2,
        max_iterations=6,
        verbose=True,
        persistent=True,
    )


def write_results(results: list[dict]) -> None:
    payload = {
        "run_metadata": {
            "generated_at": datetime.now().isoformat(),
            "model": results[0]["model"] if results else None,
            "total_queries": len(QUESTIONS),
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


def run_benchmark() -> None:
    rlm = create_rlm()
    session = PersistentSession(rlm)
    session.load(user_id="user126_paytm", storage_path=SESSION_DIR)

    results: list[dict] = []

    try:
        for index, question in enumerate(QUESTIONS, start=1):
            print("=" * 70)
            print(f"[{index}/{len(QUESTIONS)}] Question: {question}")
            print("=" * 70)

            try:
                result = rlm.completion(prompt="", root_prompt=question)
            except Exception as exc:
                print(Fore.RED + f"RLM call failed: {exc}")
                entry = {
                    "question": question,
                    "response": None,
                    "comment": None,
                    "error": str(exc),
                    "metrics": None,
                }
                results.append(entry)
                write_results(results)
                continue

            iterations = 0
            if result.metadata:
                iterations = len(result.metadata.get("iterations", []))
            metrics = {
                "iterations": iterations,
                "execution_time_seconds": result.execution_time,
                "input_tokens": result.usage_summary.total_input_tokens,
                "output_tokens": result.usage_summary.total_output_tokens,
            }

            print(Fore.LIGHTYELLOW_EX + str(result.response))
            print("-" * 70)
            print(
                f"Iterations: {metrics['iterations']} | "
                f"Time: {metrics['execution_time_seconds']:.2f}s | "
                f"Input tokens: {metrics['input_tokens']} | "
                f"Output tokens: {metrics['output_tokens']}"
            )
            comment = prompt_comment()

            entry = {
                "question": question,
                "response": result.response,
                "comment": comment,
                "model": result.root_model,
                "metrics": metrics,
                "completed_at": datetime.now().isoformat(),
            }
            results.append(entry)
            write_results(results)
    finally:
        rlm.close()

    print("=" * 70)
    print(f"Benchmark complete. Results saved to: {RESULTS_FILE}")
    print("=" * 70)


if __name__ == "__main__":
    run_benchmark()