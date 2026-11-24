"""Runs eval cases through the real production execution path.

Replaces ``evaluator.py``'s old ``_run_agent()``, which hand-rolled its own
OpenAI tool-calling loop and called raw undecorated tool functions directly
— bypassing every ``AgentMiddleware``/``FunctionMiddleware`` a real request
goes through (guardrails, HITL, and Phase 2's grounding verification).

Two paths, because ``orchestrator/modes/`` is an orchestrator-level concept
only — specialists are never mode-dispatched, in evals or in production:

- The ``orchestrator`` case goes through ``orchestrator.modes.get_mode("tool")
  .run(...)`` — the same dispatch a real ``POST /api/chat`` request uses.
- The five specialist cases go through ``shared.agent_host._run_agent_native()``
  — the real A2A entry point each specialist's ``/message:send`` handler calls.

Both paths run the agent's full ``build_specialist_middleware()`` stack, and
both go through ``shared.factory.get_chat_client()`` — so ``LLM_PROVIDER=replay``
now actually works for evals, which it never did through the old loop.
"""

from __future__ import annotations

import importlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from evals.live_limits import run_with_limits
from shared.agent_observability import get_steps, reset_steps
from shared.context import (
    current_session_id,
    current_user_email,
    current_user_role,
)
from shared.grounding.ledger import reset_grounding_ledger
from shared.guardrails.cost_budget_middleware import reset_run_cost
from shared.guardrails.flags import get_guardrail_flags, reset_guardrail_flags
from shared.log_safe import log_safe
from shared.replay_client import ReplayFixtureMissingError
from shared.run_usage import tokens_from_usage

logger = logging.getLogger(__name__)

# Agent factory registry — maps eval agent names to their creation functions.
# Lives here (not in run_evals.py) because ProductionRunner needs it to build
# specialist agents; run_evals.py imports it from here for the CLI's --agent
# choices, avoiding a run_evals <-> harness circular import.
AGENT_FACTORIES: dict[str, tuple[str, str]] = {
    "product-discovery": ("product_discovery.agent", "create_product_discovery_agent"),
    "order-management": ("order_management.agent", "create_order_management_agent"),
    "pricing-promotions": ("pricing_promotions.agent", "create_pricing_promotions_agent"),
    "review-sentiment": ("review_sentiment.agent", "create_review_sentiment_agent"),
    "inventory-fulfillment": ("inventory_fulfillment.agent", "create_inventory_fulfillment_agent"),
    "orchestrator": ("orchestrator.agent", "create_orchestrator_agent"),
}

# The specialists an orchestrator can route to: every agent but the orchestrator itself.
SPECIALISTS = frozenset(name for name in AGENT_FACTORIES if name != "orchestrator")


@dataclass
class RunOutcome:
    """What a production run produced — enough for every existing scorer plus new ones."""

    text: str
    tools_called: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    grounding: dict[str, Any] | None = None
    guardrail_flags: dict[str, bool] = field(default_factory=dict)
    error: str | None = None
    # A missing replay fixture is an infrastructure failure, not a bad answer.
    # Tracked separately so CI can say "3 fixtures are missing" instead of
    # reporting an agent that scored 0 — the ambiguity that let issue #25 sit
    # open across two PRs.
    fixture_missing: bool = False
    # Schema v2: what each tool was called with (step-shaped: tool_name +
    # tool_input), and whether the run was refused by role gating / parked for
    # human approval. All derived from the recorded steps, never from the text.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    denied: bool = False
    hitl_requested: bool = False
    # Workflow nodes entered, in order (non-``tool`` modes), and facts a target could not observe (``http``).
    nodes: list[str] = field(default_factory=list)
    not_observable: list[str] = field(default_factory=list)
    # Calls MAF parked for approval (``approval_mode="always_require"``): no step, no row, just a
    # ``function_approval_request`` in the response (``shared.agent_host.approval_requests``).
    parked_calls: list[dict[str, Any]] = field(default_factory=list)
    # Gated calls the model requested that a pre-park check refused (``shared.park_prechecks``): no approval row,
    # an explanatory sentence instead. They count as tool calls, never as a human-approval request.
    refused_calls: list[dict[str, Any]] = field(default_factory=list)
    # ``http`` target: the conversation the stack put the turn in (its ``metadata`` frame), so a next turn joins it.
    conversation_id: str | None = None
    # Orchestrator: the agents the run reported, written to the assistant message as the chat route does.
    agents_involved: list[str] = field(default_factory=list)


@dataclass
class Conversation:
    """State shared by the turns of one multi-turn case.

    ``session_id`` is what a turn binds as the session (in-process) or sends as ``conversation_id`` (``http``; empty
    until the stack names the conversation on the first turn). ``history`` is the prior user and assistant messages,
    which the in-process runner hands to the agent exactly as a real request does after loading the session.
    An in-process orchestrator turn also writes both messages to the ``messages`` table under ``session_id``
    (``_ConversationWriter``), so a specialist reached over A2A rehydrates the history it would in production.
    """

    session_id: str = ""
    history: list[dict[str, str]] = field(default_factory=list)

    def record_turn(self, user_input: str, outcome: RunOutcome) -> None:
        if outcome.conversation_id:
            self.session_id = outcome.conversation_id
        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": outcome.text})


class _ConversationWriter:
    """Makes an in-process orchestrator turn behave like ``orchestrator/routes/chat.py``'s ``/api/chat``.

    The route resolves or creates the caller's ``conversations`` row, reads the history *before* saving the turn
    (``get_history_as_dicts``, so ``RunContext.history`` is the table's and never a second copy), saves the user
    message, runs, then saves the assistant message with ``agent_name``/``agents_involved``/``metadata``. The
    route's inserts are inline SQL (its assistant insert is fused with usage logging), so this repeats the same
    columns rather than calling it. The conversation id is the case's own fresh UUID, so no run shares rows with
    another, and every row belongs to the caller's user.
    """

    def __init__(self, pool: Any, session_id: str, user_email: str) -> None:
        self._pool = pool
        self._session_id = session_id
        self._user_email = user_email
        self._user_message_id: Any = None

    @classmethod
    async def open(cls, session_id: str, user_email: str) -> _ConversationWriter | None:
        """The writer for this turn, or ``None`` (logged) when there is no database or the caller has no user row."""
        from shared.db import get_pool

        try:
            pool = get_pool()
        except Exception:
            logger.warning("eval.conversation_not_persisted reason=no_pool")
            return None
        user_id = await pool.fetchval("SELECT id FROM users WHERE email = $1", user_email)
        if user_id is None:
            logger.warning("eval.conversation_not_persisted reason=unknown_user user=%s", log_safe(user_email))
            return None
        # The first turn creates the row under the case's id; later turns find it. A row owned by someone else
        # under this id is never joined.
        await pool.execute(
            "INSERT INTO conversations (id, user_id, title) VALUES ($1, $2, $3) ON CONFLICT (id) DO NOTHING",
            session_id,
            user_id,
            "eval conversation",
        )
        owner = await pool.fetchval("SELECT user_id FROM conversations WHERE id = $1", session_id)
        if owner != user_id:
            raise RuntimeError("eval conversation id belongs to another user")
        return cls(pool, session_id, user_email)

    async def history(self) -> list[dict[str, str]]:
        from shared.session import get_history_as_dicts, get_history_provider

        return await get_history_as_dicts(get_history_provider(pool=self._pool), self._session_id)

    async def save_user(self, text: str) -> None:
        self._user_message_id = await self._pool.fetchval(
            """INSERT INTO messages (conversation_id, role, content, agent_name)
               VALUES ($1, 'user', $2, NULL) RETURNING id""",
            self._session_id,
            text,
        )

    async def discard_user(self) -> None:
        """A turn that got no answer is taken back, as the route does for a replay miss."""
        if self._user_message_id:
            await self._pool.execute(
                "DELETE FROM messages WHERE id = $1 AND conversation_id = $2", self._user_message_id, self._session_id
            )

    async def save_assistant(self, text: str, agents_involved: list[str], steps: list[dict[str, Any]]) -> None:
        clean = [{k: v for k, v in step.items() if k != "_live"} for step in steps[:50]]
        metadata = {"steps": clean, "agents_involved": agents_involved}
        await self._pool.execute(
            """INSERT INTO messages (conversation_id, role, content, agent_name, agents_involved, metadata)
               VALUES ($1, 'assistant', $2, 'orchestrator', $3, $4::jsonb)""",
            self._session_id,
            text,
            agents_involved,
            json.dumps(metadata, default=str),
        )
        await self._pool.execute("UPDATE conversations SET last_message_at = NOW() WHERE id = $1", self._session_id)


def _create_agent(agent_name: str) -> Any:
    if agent_name not in AGENT_FACTORIES:
        available = ", ".join(sorted(AGENT_FACTORIES.keys()))
        raise ValueError(f"Unknown agent: {agent_name!r}. Available: {available}")
    module_path, factory_name = AGENT_FACTORIES[agent_name]
    module = importlib.import_module(module_path)
    return getattr(module, factory_name)()


def _tools_called(steps: list[dict[str, Any]]) -> list[str]:
    return [s.get("tool_name", "") for s in steps if s.get("tool_name")]


def _tool_calls(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"tool_name": s["tool_name"], "tool_input": s.get("tool_input")} for s in steps if s.get("tool_name")]


def _output_field(step: dict[str, Any], key: str) -> Any:
    output = step.get("tool_output")
    return output.get(key) if isinstance(output, dict) else None


# What a role-guard refusal looks like in prose. ``shared.guardrails.roles._denial`` builds the structured
# payload (``error: permission_denied`` plus a "requires one of these roles" message); a specialist's reply
# relayed through an orchestrator, or a tool that refuses in plain words, carries only text. "restricted to a
# specific user" (a coupon) is a business rule, not a role guard, so the role words are spelled out.
_DENIAL_TEXT = re.compile(
    r"permission_denied"
    r"|don'?t have permission to perform this action"
    r"|requires one of these roles"
    r"|restricted to (?:\w+ )?(?:sellers?|admins?|admin(?:istrator)?s?|staff|support|managers?|power users?)\b",
    re.IGNORECASE,
)


def _denial_in(output: Any, depth: int = 0) -> bool:
    """True when a recorded tool output is a role-guard refusal, whatever shape the step stored it in.

    ``StepRecorderMiddleware`` stores ``_summarize(result)``: a dict when the tool returned one, a one-item list
    holding the JSON text when MAF wrapped the result in ``Content``, a JSON string, or (past 400 characters or
    for a tool that answers in prose) a plain string.
    """
    if depth > 3 or output is None:
        return False
    if isinstance(output, dict):
        if output.get("error") == "permission_denied":
            return True
        return any(isinstance(output.get(k), str) and _DENIAL_TEXT.search(output[k]) for k in ("error", "message"))
    if isinstance(output, list):
        return any(_denial_in(item, depth + 1) for item in output)
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
        except ValueError:
            return bool(_DENIAL_TEXT.search(output))
        return _denial_in(parsed, depth + 1) if isinstance(parsed, (dict, list)) else bool(_DENIAL_TEXT.search(output))
    return False


def _was_denied(steps: list[dict[str, Any]]) -> bool:
    """A tool refused the call for the caller's role (``shared.guardrails.roles._denial``)."""
    return any(_denial_in(s.get("tool_output")) for s in steps)


def _hitl_requested(steps: list[dict[str, Any]]) -> bool:
    """A step shows a destructive tool parked for approval (``pending_approval`` result).

    Rarely fires on its own: ``HITLFunctionMiddleware`` sits outside
    ``StepRecorderMiddleware`` and short-circuits before it, so a parked call
    leaves no step. ``pending_tool_approvals`` (a ``tool_approval_requests`` row)
    is what sees a parked tool-mode call; ``_hitl_rows`` (``hitl_requests``) sees
    a paused workflow.
    """
    return any(_output_field(s, "status") == "pending_approval" for s in steps)


async def _hitl_rows() -> int | None:
    """Rows in ``hitl_requests`` (``None`` when there is no database to ask)."""
    try:
        from shared.db import get_pool

        return await get_pool().fetchval("SELECT count(*) FROM hitl_requests")
    except Exception:
        return None


async def db_clock() -> datetime | None:
    """The database's own ``now()`` (``None`` when there is no database): the start of a run window.

    Compared against ``created_at`` so a skew between the eval host's clock and the database never matters.
    """
    try:
        from shared.db import get_pool

        return await get_pool().fetchval("SELECT now()")
    except Exception:
        return None


async def pending_tool_approvals(user_email: str, since: datetime | None) -> int | None:
    """Pending ``tool_approval_requests`` rows for ``user_email`` created at or after ``since``.

    This is the signal for a parked tool-mode call (``cancel_order``, ``process_refund``, ...):
    ``HITLFunctionMiddleware`` writes this table, not ``hitl_requests``, and short-circuits before the step
    recorder. ``None`` when there is no database or no window start, so a caller never reads "unknown" as "none".
    """
    if since is None:
        return None
    try:
        from shared.db import get_pool

        return await get_pool().fetchval(
            "SELECT count(*) FROM tool_approval_requests WHERE user_email = $1 AND status = 'pending' "
            "AND created_at >= $2",
            user_email,
            since,
        )
    except Exception:
        return None


def _routes(steps: list[dict[str, Any]]) -> list[str]:
    routes: list[str] = []
    for s in steps:
        if s.get("tool_name") != "call_specialist_agent":
            continue
        tool_input = s.get("tool_input")
        agent_name = tool_input.get("agent_name") if isinstance(tool_input, dict) else None
        if agent_name:
            routes.append(agent_name)
    return routes


class ProductionRunner:
    """Runs a single eval input through the real production path for one agent.

    A fresh runner per case (or reused across cases for the same agent —
    specialist agents are cached on first use, mirroring how a real process
    builds each agent once and reuses it across requests).
    """

    def __init__(
        self,
        agent_name: str,
        *,
        user_email: str = "eval@example.com",
        user_role: str = "customer",
    ) -> None:
        self.agent_name = agent_name
        self.user_email = user_email
        self.user_role = user_role
        # Agents bake the caller's role into their composed prompt at construction,
        # so a per-case role needs its own agent, not the first one built.
        self._agents: dict[str, Any] = {}
        self._run_role = user_role

    async def run(
        self,
        user_input: str,
        *,
        user_role: str | None = None,
        user_email: str | None = None,
        mode: str | None = None,
        conversation: Conversation | None = None,
    ) -> RunOutcome:
        """Run one input. ``user_role`` / ``user_email`` override the runner's defaults for this case.

        ``mode`` names an orchestration mode other than ``tool`` (orchestrator cases only). ``conversation`` makes
        this a turn of a multi-turn case: its session id is bound and its history reaches the agent.
        """
        self._run_role = user_role or self.user_role
        history = list(conversation.history) if conversation else []
        # A multi-turn orchestrator case writes its messages like the chat route, so a specialist reached over A2A
        # rehydrates the earlier turns from the table. Single-turn cases and specialist runs write nothing.
        writer: _ConversationWriter | None = None
        if conversation and conversation.session_id and self.agent_name == "orchestrator":
            try:
                writer = await _ConversationWriter.open(conversation.session_id, user_email or self.user_email)
            except RuntimeError as exc:
                return RunOutcome(text="", error=str(exc), guardrail_flags=get_guardrail_flags())
            if writer is not None:
                history = await writer.history()
        current_user_email.set(user_email or self.user_email)
        current_user_role.set(self._run_role)
        current_session_id.set(conversation.session_id if conversation else "")
        reset_steps()
        reset_grounding_ledger()
        reset_guardrail_flags()
        # The per-run token ceiling (COST_BUDGET_TOKENS_PER_RUN) counts in a ContextVar. A server resets it per
        # request; this in-process loop never did, so one eval process summed every case against one ceiling and
        # refused the rest of the suite (17 of 30 orchestrator cases on the first reference run).
        reset_run_cost()

        if writer is not None:
            await writer.save_user(user_input)

        hitl_rows_before = await _hitl_rows()
        run_email = user_email or self.user_email
        window_start = await db_clock()

        async def _attempt() -> RunOutcome:
            # A retried attempt starts clean, or its steps would be counted twice.
            reset_steps()
            reset_grounding_ledger()
            reset_guardrail_flags()
            if mode and mode != "tool" and self.agent_name != "orchestrator":
                raise ValueError(f"mode {mode!r} is an orchestrator mode; agent {self.agent_name!r} has none")
            if self.agent_name == "orchestrator":
                return await self._run_orchestrator(
                    user_input, mode or "tool", history, conversation.session_id if conversation else None
                )
            return await self._run_specialist(user_input, history)

        try:
            # A plain call unless a live run configured retries (429 / 5xx backoff, evals/live_limits.py).
            outcome = await run_with_limits(_attempt)
        except ReplayFixtureMissingError as exc:
            if writer is not None:
                await writer.discard_user()
            return RunOutcome(
                text="",
                error=str(exc),
                fixture_missing=True,
                guardrail_flags=get_guardrail_flags(),
            )
        except Exception as exc:
            if writer is not None:
                await writer.discard_user()
            # A specialist's missing fixture reaches the orchestrator as an A2A
            # failure rather than the original exception type, so fall back to
            # recognising it by message.
            return RunOutcome(
                text="",
                error=str(exc),
                fixture_missing="No replay fixture" in str(exc),
                guardrail_flags=get_guardrail_flags(),
            )

        outcome.guardrail_flags = get_guardrail_flags()
        steps = get_steps()
        if writer is not None:
            await writer.save_assistant(outcome.text, outcome.agents_involved or ["orchestrator"], steps)
        outcome.tool_calls = _tool_calls(steps) + outcome.parked_calls + outcome.refused_calls
        outcome.denied = outcome.denied or _was_denied(steps)
        hitl_rows_after = await _hitl_rows()
        parked_tool_calls = await pending_tool_approvals(run_email, window_start)
        outcome.hitl_requested = (
            outcome.hitl_requested
            or _hitl_requested(steps)
            or bool(parked_tool_calls)
            or bool(outcome.parked_calls)
            or (hitl_rows_before is not None and hitl_rows_after is not None and hitl_rows_after > hitl_rows_before)
        )
        return outcome

    async def _run_orchestrator(
        self,
        user_input: str,
        mode_name: str = "tool",
        history: list[dict[str, str]] | None = None,
        conversation_id: str | None = None,
    ) -> RunOutcome:
        from orchestrator.modes import RunContext, get_mode

        mode = get_mode(mode_name)
        ctx = RunContext(history=history or [], conversation_id=conversation_id)
        text = ""
        agents_involved: list[str] = []
        grounding: dict[str, Any] | None = None
        usage: dict[str, Any] = {}
        nodes: list[str] = []
        parked = False

        async for event in mode.run(user_input, ctx):
            if event.kind == "run_completed":
                text = event.payload.get("text", "")
                agents_involved = list(event.payload.get("agents_involved") or [])
                grounding = event.payload.get("grounding")
                usage = event.payload.get("usage") or {}
            elif event.kind == "node_enter" and event.node_id:
                nodes.append(event.node_id)
            elif event.kind == "request_info":
                # A workflow paused for a human (the return gate): the same fact the ``http`` target reads
                # from the ``request_info`` frame.
                parked = True

        steps = get_steps()
        routes = _routes(steps)
        if not routes and mode_name != "tool":
            # A mesh or a workflow names its specialists in ``agents_involved`` (there is no ``call_specialist_agent``
            # step to read), so the specialists it reached are its routes.
            routes = [a for a in agents_involved if a in SPECIALISTS]
        # run_completed.usage is {total, by_agent}: the orchestrator's calls plus
        # every specialist's, as reported over A2A.
        tokens_in, tokens_out = tokens_from_usage(usage)
        return RunOutcome(
            text=text,
            tools_called=_tools_called(steps),
            routes=routes,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            grounding=grounding,
            nodes=nodes,
            hitl_requested=parked,
            agents_involved=agents_involved,
        )

    async def _run_specialist(self, user_input: str, history: list[dict[str, str]] | None = None) -> RunOutcome:
        from shared.agent_host import _run_agent_native

        agent = self._agents.get(self._run_role)
        if agent is None:
            agent = self._agents[self._run_role] = _create_agent(self.agent_name)

        metadata_box: dict[str, Any] = {}
        text = await _run_agent_native(agent, user_input, history=history or None, metadata_box=metadata_box)

        steps = get_steps()
        usage = metadata_box.get("_maf_usage") or {}
        requested = metadata_box.get("_maf_approval_requests") or []
        parked = [p for p in requested if not p.get("refused")]
        refused = [{k: v for k, v in p.items() if k != "refused"} for p in requested if p.get("refused")]
        role_denied = any(p.get("refused") == "role" for p in requested)
        return RunOutcome(
            text=text,
            tools_called=_tools_called(steps) + [p["tool_name"] for p in requested],
            parked_calls=list(parked),
            refused_calls=refused,
            denied=role_denied,
            tokens_in=usage.get("input_token_count") or 0,
            tokens_out=usage.get("output_token_count") or 0,
            grounding=metadata_box.get("grounding"),
        )
