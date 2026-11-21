"""ASGI entry point: `uvicorn mizan.server:app`.

Reads DATABASE_URL from the environment. The chat endpoint uses Gemini when GEMINI_API_KEY is
set and answers 503 otherwise. All other endpoints work without any model.
"""

import os

from mizan.agent.gemini import GeminiLLM
from mizan.agent.types import AgentLLM
from mizan.api import create_app


def _llm_from_env() -> AgentLLM | None:
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return GeminiLLM.from_env()
    return None


app = create_app(os.environ["DATABASE_URL"], _llm_from_env())
