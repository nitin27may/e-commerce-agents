"""A real Python specialist on a real socket around a scripted model, for the cross-process A2A interop check.

The Python twin of ``agents/dotnet/tests/ECommerceAgents.A2AInteropHost``: the production specialist app
(``create_agent_app``, MAF's tool loop, the step recorder, ``AgentAuthMiddleware``, the A2A 1.0 surface) with
only the model scripted (``tests/a2a_support.py``), so a .NET client can be pointed at it with no key and no
database. ``scripts/a2a-interop.sh`` starts it and runs the .NET interop tests against it::

    cd agents/python && uv run python -m tests.a2a_interop_server --port 18091 [--mode ok|boom|miss]
"""

from __future__ import annotations

import argparse

import uvicorn

from shared.config import settings
from tests.a2a_support import SECRET, build_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18091)
    parser.add_argument("--mode", default="ok", choices=["ok", "boom", "miss"])
    args = parser.parse_args()

    # The same settings tests/a2a_support.apply_env sets under pytest, applied for the whole process.
    settings.AGENT_SHARED_SECRET = SECRET
    settings.AUTH_MODE = "local"
    settings.LLM_PROVIDER = "openai"

    app, _ = build_app(args.mode)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
