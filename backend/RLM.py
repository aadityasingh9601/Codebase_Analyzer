from rlm import RLM
from rlm.core.types import UsageSummary
from dotenv import load_dotenv, dotenv_values
from openai import OpenAI
from testdata import eat_that_frog
import os
load_dotenv()

ai_api_key: str = os.getenv("AI_API_KEY")

custom_prompt=""" Write your custom prompt here """

# Add a list of free models here, get the most out of them, don't just exhaust the most powerful LLM for small tasks.
# Open router supports the features of smartly selecting LLM based on task complexity, use that or write logic yourself.
# But the main issue you might face will be Rate-limit exceeded, not model exhausted usually.

# Free models you can choose & experiment with ->
# "nvidia/nemotron-3-ultra-550b-a55b:free"
# "poolside/laguna-s-2.1:free"

rlm = RLM(
    backend="openrouter",
    backend_kwargs={"model_name":"nvidia/nemotron-3-ultra-550b-a55b:free" , "api_key":ai_api_key},
    environment="local",
    environment_kwargs = {
    "setup_code": "import numpy as np",  # Run before each completion
    },
    max_depth=2, # Add these accordingly for needs dynamically
    max_iterations=10, # Adjust these accordingly as per needs
    # custom_system_prompt=custom_prompt
    verbose=True,  # For printing to console with rich, disabled by default.
    persistent=True,
)

# We'll use local or Docker REPL to run the REPL environment required by the RLM.
# prompt: The main context/input (string or dict). This becomes the context variable in the REPL.
# root_prompt: Optional hint shown to the root LM (useful for Q&A tasks).

result = rlm.completion(
    prompt=eat_that_frog,
root_prompt="""Count the occurenes of all the alphabets from A-Z both capital & small in the given string & print the count
,each on a new line."""
)

print(result.response)         
print("Execution time ->",result.execution_time)    
print("Metadata ->",result.metadata)
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

count = eat_that_frog.count("e")
print(count)