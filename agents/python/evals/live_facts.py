"""Run-time facts for live eval datasets: ``{{order:1}}`` and ``{{product_id:Sony WH-1000XM5}}``.

The replay datasets hard-code order ids that no seeded database contains (``order_id_for`` in
``scripts/seed.py`` is a uuid5 over the loop index) and run as an ``eval@example.com`` user the seeder never
creates. The replay fixtures recorded exactly that: the tools answered "order not found". That is a valid
regression signal for replay and a wrong measurement for a live model, which gets "not found" and scores
groundedness 0 for answering honestly.

A live case instead names the facts it needs and this module resolves them against the database the run
uses, right before the case runs, so the resolved text is what the model sees and what the run record stores:

    ``{{order:1}}``          the case user's most recent order (``{{order:2}}`` the one before it)
    ``{{order:delivered}}``  the case user's most recent order in that status
    ``{{product_id:NAME}}``  the id of the product with that exact (case-insensitive) name

Resolution applies only to a case whose dataset is a ``*.live.json`` file or that carries ``"live": true``.
Replay datasets never contain a placeholder (``tests/test_eval_live_facts.py`` pins it), so their request
text, and with it every replay fixture key, is untouched. It covers ``input`` and every string inside
``expected_tool_args``, and the same two fields of every turn of a multi-turn case. An unresolvable placeholder
raises: a live dataset that cannot find its facts is a setup problem, not a score.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from evals.evaluator import EvalCase

PLACEHOLDER = re.compile(r"\{\{\s*(order|product_id)\s*:\s*([^{}]*?)\s*\}\}")
LIVE_SUFFIX = ".live.json"


class LiveFactError(RuntimeError):
    """A placeholder could not be resolved against the database."""


class FactSource(Protocol):
    """Where run-time facts come from. The in-process target uses the database pool."""

    async def order_id(self, user_email: str, selector: str) -> str | None: ...

    async def product_id(self, name: str) -> str | None: ...


class PoolFactSource:
    """Facts straight from Postgres (``shared.db.get_pool()``): read-only, parameterised."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def order_id(self, user_email: str, selector: str) -> str | None:
        if selector.isdigit():
            position, status = int(selector), None
        else:
            position, status = 1, selector.lower()
        if position < 1:
            return None
        row = await self._pool.fetchval(
            """SELECT o.id FROM orders o JOIN users u ON u.id = o.user_id
               WHERE u.email = $1 AND ($2::text IS NULL OR o.status = $2)
               ORDER BY o.created_at DESC, o.id
               OFFSET $3 LIMIT 1""",
            user_email,
            status,
            position - 1,
        )
        return None if row is None else str(row)

    async def product_id(self, name: str) -> str | None:
        row = await self._pool.fetchval(
            "SELECT id FROM products WHERE lower(name) = lower($1) ORDER BY id LIMIT 1", name
        )
        return None if row is None else str(row)


def has_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return PLACEHOLDER.search(value) is not None
    if isinstance(value, Mapping):
        return any(has_placeholder(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(has_placeholder(v) for v in value)
    return False


async def _resolve_text(text: str, user_email: str | None, source: FactSource) -> str:
    out: list[str] = []
    last = 0
    for match in PLACEHOLDER.finditer(text):
        kind, arg = match.group(1), match.group(2)
        if kind == "order":
            if not user_email:
                raise LiveFactError(f"{match.group(0)} needs the case to set user_email (orders belong to a user)")
            found = await source.order_id(user_email, arg)
            if found is None:
                raise LiveFactError(f"{match.group(0)}: {user_email} has no such order in this database")
        else:
            found = await source.product_id(arg)
            if found is None:
                raise LiveFactError(f"{match.group(0)}: no product with that name in this database")
        out.append(text[last : match.start()])
        out.append(found)
        last = match.end()
    out.append(text[last:])
    return "".join(out)


async def _resolve_value(value: Any, user_email: str | None, source: FactSource) -> Any:
    if isinstance(value, str):
        return await _resolve_text(value, user_email, source)
    if isinstance(value, Mapping):
        return {k: await _resolve_value(v, user_email, source) for k, v in value.items()}
    if isinstance(value, list):
        return [await _resolve_value(v, user_email, source) for v in value]
    return value


async def resolve_case(case: EvalCase, source: FactSource) -> EvalCase:
    """The case with every placeholder replaced. A case that is not ``live`` is returned as is."""
    if not case.live:
        return case
    turns = None
    if case.turns:
        # Each turn resolves like ``input`` does; the case's own ``input`` is its last turn's message.
        turns = [
            replace(
                turn,
                input=await _resolve_text(turn.input, case.user_email, source),
                expected_tool_args=await _resolve_value(turn.expected_tool_args, case.user_email, source),
            )
            for turn in case.turns
        ]
    return replace(
        case,
        input=turns[-1].input if turns else await _resolve_text(case.input, case.user_email, source),
        expected_tool_args=await _resolve_value(case.expected_tool_args, case.user_email, source),
        turns=turns,
    )


async def resolve_case_from_database(case: EvalCase) -> EvalCase:
    """``resolve_case`` against the run's own database pool, only when there is something to resolve."""
    if not case.live or not (
        has_placeholder(case.input)
        or has_placeholder(case.expected_tool_args)
        or has_placeholder([(t.input, t.expected_tool_args) for t in case.turns or []])
    ):
        return case
    from shared.db import get_pool

    try:
        pool = get_pool()
    except RuntimeError as exc:
        raise LiveFactError(
            "a live dataset resolves orders and products from the database the run uses, and this process has no "
            "database pool (set DATABASE_URL to the database the stack under test serves)"
        ) from exc
    return await resolve_case(case, PoolFactSource(pool))
