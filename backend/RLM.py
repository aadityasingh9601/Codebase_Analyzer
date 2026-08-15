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
# But the main issue you might face will be Rate-limit exceeded, not model exhausted usually.

rlm = RLM(
    backend="openrouter",
    backend_kwargs={"model_name": "poolside/laguna-s-2.1:free", "api_key":ai_api_key},
    environment="local",
    environment_kwargs = {
    "setup_code": "import numpy as np",  # Run before each completion
    },
    max_depth=2, # Add these accordingly for needs dynamically
    max_iterations=30, # Adjust these accordingly as per needs
    # custom_system_prompt=custom_prompt
    verbose=True,  # For printing to console with rich, disabled by default.
    persistent=True,
)

# We'll use local or Docker REPL to run the REPL environment required by the RLM.
# prompt: The main context/input (string or dict). This becomes the context variable in the REPL.
# root_prompt: Optional hint shown to the root LM (useful for Q&A tasks).

result = rlm.completion(
    prompt="""
Introduction …………. Eat That Frog
Chapter 1...................... Set the Table
Chapter 2...................... Plan Every Day In Advance
Chapter 3...................... Apply the 80/20 Rule to Everything
Chapter 4...................... Consider the Consequences
Chapter 5………………Practice Creative Procrastination
Chapter 6...................... Use the ABCDE Method Continually
Chapter 7...................... Focus on Key Result Areas
Chapter 8...................... The Law of Three
Chapter 9...................... Prepare Thoroughly Before You Begin
Chapter 10……………..Take It One Oil Barrel at a Time
Chapter 11 .................... Upgrade Your Key Skills
Chapter 12 .................... Leverage Your Special Talents
Chapter 13 .................... Identify Your Key Constraints
Chapter 14 .................... Put the Pressure on Yourself
EAT THAT FROG!
PAGE 2
Chapter 15 .................... Maximize Your Personal Power
Chapter 16 .................... Motivate Yourself into Action
Chapter 17 .................... Get Out of the Technological Time Sinks
Chapter 18 .................... Slice and Dice the Task
Chapter 19 .................... Create Large Chunks of Time
Chapter 20 .................... Develop a Sense of Urgency
Chapter 21 .................... Single Handle Every Task""",
root_prompt="""List all the chapters names in the given data, each on a new line."""
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