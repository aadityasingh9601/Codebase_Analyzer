from rlm import RLM
from rlm.core.types import UsageSummary
from dotenv import load_dotenv, dotenv_values
from openai import OpenAI
import os
load_dotenv()

ai_api_key: str = os.getenv("AI_API_KEY")

custom_prompt=""" Write your custom prompt here """

# Add a list of free models here, get the most out of them, don't just exhaust the most powerful LLM for small tasks.
# Open router supports the features of smartly selecting LLM based on task complexity, use that or write logic yourself.

rlm = RLM(
    backend="openrouter",
    backend_kwargs={"model_name": "poolside/laguna-s-2.1:free", "api_key":ai_api_key},
    environment="local",
    environment_kwargs = {
    "setup_code": "import numpy as np",  # Run before each completion
    },
    max_depth=2, # Add these accordingly for needs dynamically
    max_iterations=10, # Adjust these accordingly as per needs
    # custom_system_prompt=custom_prompt
    verbose=True,  # For printing to console with rich, disabled by default.
)

# We'll use local or Docker REPL to run the REPL environment required by the RLM.

result = rlm.completion("Print me the first 100 powers of two, each on a newline.")
# result = rlm.completion(
#     "Calculate the factorial of 100 and return the number of digits."
# )

# print(result.response)          # "158"
# print(result.execution_time)    # 12.34
# print(result.metadata)          # Trajectory dict (if logger provided), else None
print(result.usage_summary.to_dict())

# usage: UsageSummary = result.usage_summary
# usage.to_dict()
# {
#     "model_usage_summaries": {
#         "gpt-4o": {
#             "total_calls": 5,
#             "total_input_tokens": 15000,
#             "total_output_tokens": 2000
#         }
#     }
# }