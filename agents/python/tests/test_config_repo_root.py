"""_resolve_repo_root — regression test for a real Docker crash.

Every dockerized agent (orchestrator + all 5 specialists) crashed at import
time with `IndexError: 3`: config.py's old `Path(__file__).resolve().parents[3]`
assumed the host's <repo>/agents/python/shared/ layout, but the agent
Dockerfile's build context is ./agents, so inside the image config.py lands
flatly at /app/shared/config.py with only 2 real parents. Caught via a live
`docker compose up` — every container exited immediately, meaning this path
had never actually been exercised end-to-end before.
"""

from __future__ import annotations

from pathlib import Path

from shared.config import _resolve_repo_root


def test_resolves_three_levels_up_on_the_host_layout() -> None:
    # <repo>/agents/python/shared/config.py -> <repo>
    host_path = Path("/home/user/e-commerce-agents/agents/python/shared/config.py")
    assert _resolve_repo_root(host_path) == Path("/home/user/e-commerce-agents")


def test_falls_back_to_the_immediate_parent_on_the_shallow_docker_layout() -> None:
    # /app/shared/config.py -> only 2 real parents (/app, /) — parents[3]
    # would raise IndexError; must fall back instead of crashing.
    docker_path = Path("/app/shared/config.py")
    assert _resolve_repo_root(docker_path) == Path("/app/shared")
