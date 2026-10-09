import os

# Keep the CLI output clean: pydantic-ai prints a promotional banner on the first agent run.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

__version__ = "0.1.0"
