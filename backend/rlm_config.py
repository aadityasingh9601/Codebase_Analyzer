
import os

from dotenv import load_dotenv
from rlm import RLM

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FULL_JSON_PATH = os.path.join(SCRIPT_DIR, "codebase_paytm.json")
LIGHTWEIGHT_JSON_PATH = os.path.join(SCRIPT_DIR, "codebase_paytm_lightweight.json")
SESSION_DIR = os.path.join(SCRIPT_DIR, "rlm_sessions")

ai_api_key: str = os.getenv("AI_API_KEY")

CODEBASE_JSON_ABS = os.path.join(SCRIPT_DIR, "codebase_paytm.json")
LIGHTWEIGHT_JSON_ABS = os.path.join(SCRIPT_DIR, "codebase_paytm_lightweight.json")

SETUP_CODE = (
    f'_CODEBASE_JSON = r"{CODEBASE_JSON_ABS}"\n'
    + open(os.path.join(SCRIPT_DIR, "codebase_helpers.py")).read()
    + "\n"
)


def create_rlm(logger=None, model_name: str = "inclusionai/ling-3.0-flash-fin:free") -> RLM:
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
        max_iterations=8,
        max_errors=3,
        max_timeout=240,
        max_tokens=200_000,
        sampling_args={"temperature": 0.2},
        verbose=True,
        persistent=True,
    )