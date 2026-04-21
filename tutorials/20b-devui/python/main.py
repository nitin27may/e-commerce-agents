"""MAF v1 — Ch20b: DevUI quickstart.

Registers a single Agent with DevUI's serve() helper and launches the
browser dashboard on localhost:8090. DevUI is a dev-only, Python-only
harness that exposes an OpenAI-compatible Responses API plus a live
tracing panel.

Run:
    uv run python main.py
Then open http://localhost:8090
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent  # noqa: E402
from agent_framework.devui import serve  # noqa: E402
from agent_framework.openai import (  # noqa: E402
    OpenAIChatClient,
    OpenAIChatCompletionClient,
)


def _client():
    """Pick a chat client based on LLM_PROVIDER, matching the series convention."""
    if os.environ.get("LLM_PROVIDER", "openai").lower() == "azure":
        return OpenAIChatCompletionClient(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_KEY"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
    return OpenAIChatClient(
        model=os.environ.get("LLM_MODEL", "gpt-4.1"),
        api_key=os.environ["OPENAI_API_KEY"],
    )


def build_agent() -> Agent:
    """Single demo agent — DevUI registers it under the id 'devui-demo'."""
    return Agent(
        _client(),
        instructions="You are a friendly e-commerce assistant for a demo store.",
        name="devui-demo",
        description="Demo agent registered with MAF DevUI",
    )


if __name__ == "__main__":
    # DevUI will open the browser at http://localhost:8090 and stream
    # OpenTelemetry spans into its tracing tab for every run.
    serve(
        entities=[build_agent()],
        port=8090,
        auto_open=True,
        instrumentation_enabled=True,
    )
