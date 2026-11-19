"""Browse this repo's agents and workflows in MAF DevUI (dev only, Python only).

DevUI is Microsoft Agent Framework's local web harness: pick an entity, chat with it, and watch the traces and
tool calls live. This script registers the five specialists and the two workflows::

    product-discovery  order-management  pricing-promotions  review-sentiment  inventory-fulfillment
    pre-purchase (fan-out/fan-in)  return-and-replace (policy gate + approval pause)

Run it from the repo root. DevUI is not a dependency of this project (it requires ``fastapi<0.163``, and putting it in
the lock would downgrade the orchestrator's production FastAPI), so it is added to the run only::

    uv run --project agents/python --with agent-framework-devui==1.0.0b261112 python -m scripts.devui

The version is the one ``tutorials/pyproject.toml`` pins. Needs the database (``./scripts/dev.sh --infra-only``, then
``--seed-only``) because the tools read it. Listing and browsing the entities needs no model key; chatting needs one.

Identity. The tools read who is asking from ContextVars (``shared/context.py``), which the orchestrator sets from the
request. DevUI has no such request, so one fixed identity is bound for the whole process::

    DEVUI_USER_EMAIL   default alice.johnson@gmail.com   (a seeded customer)
    DEVUI_USER_ROLE    default customer                  (customer | power_user | seller | support | admin)

That identity is real: with ``admin`` the tools can do what an admin can, to the database in ``DATABASE_URL``. Use a
database you can throw away.

DevUI binds to 127.0.0.1. A non-loopback ``--host`` is refused unless ``DEVUI_AUTH_TOKEN`` is set, because the entities
run real tools as the identity above.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from typing import Any

logger = logging.getLogger("devui")

DEFAULT_EMAIL = "alice.johnson@gmail.com"
DEFAULT_ROLE = "customer"
ROLES = ("customer", "power_user", "seller", "support", "admin")
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def resolve_identity(email: str | None = None, role: str | None = None) -> tuple[str, str]:
    """The identity DevUI's tools will run as: arguments, else ``DEVUI_USER_*``, else a seeded customer.

    Raises ``ValueError`` for an unknown role rather than silently becoming a customer.
    """
    email = (email or os.environ.get("DEVUI_USER_EMAIL") or DEFAULT_EMAIL).strip().lower()
    role = (role or os.environ.get("DEVUI_USER_ROLE") or DEFAULT_ROLE).strip().lower()
    if role not in ROLES:
        raise ValueError(f"DEVUI_USER_ROLE must be one of {', '.join(ROLES)} (got {role!r})")
    return email, role


def bind_dev_identity(email: str, role: str) -> None:
    """Bind the identity every tool reads (``shared/context.py``) in the current context.

    Must run before the agents are built: ``get_system_prompt(current_user_role.get())`` composes a role-aware prompt at
    construction time.
    """
    from shared.context import current_session_id, current_user_email, current_user_role

    current_user_email.set(email)
    current_user_role.set(role)
    current_session_id.set(f"devui-{uuid.uuid4()}")


def build_entities() -> list[Any]:
    """The five specialist agents and the two workflows, ready to register with DevUI.

    Imports nothing from DevUI, so it is testable without it. Call :func:`bind_dev_identity` first.
    """
    from inventory_fulfillment.agent import create_inventory_fulfillment_agent
    from orchestrator.modes.workflow_mode import PrePurchaseMode, ReturnReplaceMode
    from order_management.agent import create_order_management_agent
    from pricing_promotions.agent import create_pricing_promotions_agent
    from product_discovery.agent import create_product_discovery_agent
    from review_sentiment.agent import create_review_sentiment_agent
    from workflows.pre_purchase import PrePurchaseWorkflow
    from workflows.return_replace import ReturnAndReplaceWorkflow

    agents = [
        create_product_discovery_agent(),
        create_order_management_agent(),
        create_pricing_promotions_agent(),
        create_review_sentiment_agent(),
        create_inventory_fulfillment_agent(),
    ]
    # The modes know which real tools each workflow needs; reuse them instead of listing the tools twice.
    workflows = [
        PrePurchaseWorkflow(PrePurchaseMode()._resolve_tools())._build_maf_workflow(),  # noqa: SLF001
        ReturnAndReplaceWorkflow(ReturnReplaceMode()._resolve_tools())._build_maf_workflow(),  # noqa: SLF001
    ]
    return [*agents, *workflows]


def check_bind(host: str, token: str | None) -> None:
    """Refuse a non-loopback bind with no auth token: these entities run real tools as the bound identity."""
    if host not in LOOPBACK and not token:
        raise SystemExit(
            f"Refusing to bind DevUI to {host!r} without DEVUI_AUTH_TOKEN: the agents here run real tools "
            "as the bound identity. Use 127.0.0.1, or set a token."
        )


async def _serve(entities: list[Any], host: str, port: int, token: str | None, open_browser: bool) -> None:
    import uvicorn
    from agent_framework_devui import DevServer

    from shared.db import close_db_pool, init_db_pool

    # The pool is created in the same event loop that serves requests (asyncpg pools belong to their loop), which is
    # why this drives uvicorn itself instead of calling devui.serve(), which owns a loop of its own.
    await init_db_pool()
    try:
        server = DevServer(
            port=port, host=host, ui_enabled=True, mode="developer", auth_enabled=bool(token), auth_token=token
        )
        server.set_pending_entities(entities)
        app = server.get_app()
        if open_browser:
            import webbrowser

            webbrowser.open(f"http://{host}:{port}")
        logger.info("DevUI on http://%s:%s with %d entities", host, port, len(entities))
        await uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info")).serve()
    finally:
        await close_db_pool()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Browse the agents and workflows in MAF DevUI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--open", action="store_true", help="open the browser")
    args = parser.parse_args(argv)

    try:
        import agent_framework_devui  # noqa: F401
    except ImportError:
        print(
            "DevUI is not installed here. Run it with:\n"
            "  uv run --project agents/python --with agent-framework-devui==1.0.0b261112 python -m scripts.devui",
            file=sys.stderr,
        )
        return 2

    token = os.environ.get("DEVUI_AUTH_TOKEN") or None
    check_bind(args.host, token)
    email, role = resolve_identity()
    logging.basicConfig(level=logging.INFO)
    logger.info("tools will run as %s (%s)", email, role)

    async def run() -> None:
        # Identity first (the prompts are role-aware), then the entities, all in the loop's own context so every
        # request task inherits it.
        bind_dev_identity(email, role)
        await _serve(build_entities(), args.host, args.port, token, args.open)

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
