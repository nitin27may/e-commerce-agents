"""The ``http`` eval target: drive a running orchestrator over ``POST /api/chat/stream`` and write the same
run records the in-process target writes.

``python -m evals.run_evals --target http --base-url http://localhost:8080 --stack dotnet ...`` logs in as a
seeded user, sends each case as a chat turn and assembles the SSE frames into a ``RunOutcome`` (the value
``evals/harness.py::ProductionRunner`` returns), so scoring, records, baselines and ``--rescore`` are untouched.
It needs a base URL and nothing else: no database, no model key, no access to any process. It works against the
Python stack, the .NET stack, the mixed stacks and any deployed URL, because it only speaks the wire the web UI
speaks (``web/src/lib/api.ts::chatStream``).

What the stream carries, and what each frame becomes:

=========================  ==============================================================================
frame                      becomes
=========================  ==============================================================================
plain ``data:``            response text (the orchestrator's own final answer, concatenated)
``event: delta``           nothing: a specialist's live preview, restated by the final text
``event: step``            a tool call (name, arguments), a denial (``permission_denied`` output), a route
                           (``call_specialist_agent``), a parked approval (``pending_approval`` status)
``event: grounding``       the grounding report, hence ``context`` and the groundedness score
``event: node``            workflow nodes entered, in order (``RunRecord.nodes``)
``event: request_info``    a workflow paused for a human: ``hitl_requested``
``event: run``             run id (used to read tokens back) and ``pending_approval``
``event: metadata``        terminator; ``agents_involved`` is not used
``event: replay_miss``     a replay fixture was missing: ``fixture_missing``
``event: error``           a mode failed: the case errors
=========================  ==============================================================================

What it cannot fill, and how the record says so (``RunRecord.not_observable``): ``guardrail_flags`` (a server
side ContextVar, never on the wire), ``hitl`` when a tool-mode specialist parked a destructive call (the park
leaves no step and no frame; workflow modes do emit ``request_info``, so there the absence is real),
``tokens`` and ``cost_usd`` when ``GET /api/runs`` does not list the run, and ``grounding`` when the stack
sent no grounding frame (``GROUNDING_MODE=off``). The scorer treats an assertion on a not observable fact as not
applicable. ``context`` is the grounding evidence lines, exactly as in-process: raw tool output is not captured
by either target.

One honest difference from the in-process target: it drives the *orchestrator* only. A specialist dataset
(``--agent product-discovery``) is therefore routed by the orchestrator, and the specialist's steps come back
through it. That is an end to end measurement, not the same measurement as calling the specialist directly.
Under ``LLM_PROVIDER=replay`` a logged-in user's profile and recent orders are part of the model request, so the
committed fixtures (recorded for an identity with no profile) only serve cases that call no model; see
``docs/agent-quality.md``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from evals.harness import RunOutcome, _hitl_requested, _routes, _was_denied
from evals.records import Setup

logger = logging.getLogger(__name__)

# Seeded demo accounts (scripts/seed.py USERS and SUPPORT_EMAIL; tests/test_eval_http_target.py keeps this table
# and the seeder in step). Published demo passwords, not secrets; an operator overrides them with the variables
# documented in evals/README.md.
SEEDED_PASSWORDS: dict[str, str] = {
    "admin.demo@gmail.com": "admin123",
    "support.demo@gmail.com": "support123",
    "power.demo@gmail.com": "power123",
    "power2.demo@gmail.com": "power123",
    "seller.demo@gmail.com": "seller123",
    "seller2.demo@gmail.com": "seller123",
    **{
        f"{name}@gmail.com": "customer123"
        for name in (
            "alice.johnson",
            "bob.smith",
            "carol.davis",
            "dave.wilson",
            "emma.brown",
            "frank.miller",
            "grace.lee",
            "henry.taylor",
            "iris.chen",
            "jack.anderson",
            "kate.martinez",
            "leo.garcia",
            "mia.robinson",
            "noah.thomas",
            "olivia.white",
        )
    },
}
STAFF_ROLES = frozenset({"admin", "support", "seller"})
# One account per role when a case names a role and no email.
ROLE_ACCOUNTS: dict[str, str] = {
    "customer": "alice.johnson@gmail.com",
    "power_user": "power.demo@gmail.com",
    "seller": "seller.demo@gmail.com",
    "support": "support.demo@gmail.com",
    "admin": "admin.demo@gmail.com",
}
DEFAULT_ROLE = "customer"

DEFAULT_TIMEOUT_S = 180.0
MAX_RATE_LIMIT_RETRIES = 4
MAX_BACKOFF_S = 30.0
_NOT_FILLED_ALWAYS = ("guardrail_flags",)


class HttpTargetError(RuntimeError):
    """A turn could not be run (login, rate limit, transport). Becomes the case's ``error``, never a score."""


# ───────────────────────────── SSE parsing ─────────────────────────────


@dataclass(frozen=True)
class SseFrame:
    event: str
    data: str


def parse_sse(text: str) -> list[SseFrame]:
    """Every complete frame in ``text`` (a whole body). The streaming reader uses ``SseParser``."""
    parser = SseParser()
    return [*parser.feed(text), *parser.finish()]


class SseParser:
    """Incremental SSE parser with the semantics ``web/src/lib/api.ts::chatStream`` has.

    A frame ends at a blank line; ``event:`` names it (empty means the plain text frame); several ``data:``
    lines are joined with ``\\n`` (SSE spec 9.2.6, which is how a text token that contains a newline arrives).
    ``\\r\\n`` and ``\\r`` are line breaks too, as ``shared/sse.py::sse_frame`` writes them, and a trailing
    ``\\r`` waits for the next chunk since it may be half of a ``\\r\\n``. A frame with no ``data:`` line is
    skipped, as the web client does.
    """

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> list[SseFrame]:
        self._buffer += chunk
        held = ""
        if self._buffer.endswith("\r"):
            held, self._buffer = "\r", self._buffer[:-1]
        text = self._buffer.replace("\r\n", "\n").replace("\r", "\n")
        blocks = text.split("\n\n")
        self._buffer = blocks.pop() + held
        return [frame for block in blocks if (frame := self._frame(block)) is not None]

    def finish(self) -> list[SseFrame]:
        """A last frame the server never terminated with a blank line (a dropped stream may leave one)."""
        rest = self._buffer.replace("\r\n", "\n").replace("\r", "\n")
        self._buffer = ""
        frame = self._frame(rest) if rest.strip() else None
        return [frame] if frame is not None else []

    @staticmethod
    def _frame(block: str) -> SseFrame | None:
        event = ""
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                value = line[5:]
                data.append(value[1:] if value.startswith(" ") else value)
        if not data:
            return None
        return SseFrame(event=event, data="\n".join(data))


# ───────────────────────────── Frames to an outcome ─────────────────────────────


@dataclass
class StreamResult:
    """Everything one chat stream said, before it becomes a ``RunOutcome``."""

    text: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[str] = field(default_factory=list)
    grounding: dict[str, Any] | None = None
    run_id: str | None = None
    pending_approval: bool = False
    request_info: bool = False
    replay_miss: bool = False
    errors: list[str] = field(default_factory=list)
    saw_terminator: bool = False
    frames: int = 0

    @property
    def response(self) -> str:
        return "".join(self.text)


def _json(data: str) -> Any:
    import json

    try:
        return json.loads(data)
    except ValueError:
        return None


def absorb(result: StreamResult, frame: SseFrame) -> None:
    """Fold one frame into ``result``. Unknown frames are ignored, as the web client ignores them."""
    result.frames += 1
    name = frame.event
    if frame.data == "[DONE]":
        result.saw_terminator = True
        return
    if name == "":
        result.text.append(frame.data)
    elif name == "step":
        step = _json(frame.data)
        if isinstance(step, dict):
            result.steps.append(step)
    elif name == "grounding":
        report = _json(frame.data)
        if isinstance(report, dict):
            result.grounding = report
    elif name == "node":
        node = _json(frame.data)
        if isinstance(node, dict) and node.get("phase") == "enter" and node.get("node_id"):
            result.nodes.append(str(node["node_id"]))
    elif name == "request_info" or name == "approval":
        result.request_info = True
    elif name == "run":
        run = _json(frame.data)
        if isinstance(run, dict):
            result.run_id = str(run["run_id"]) if run.get("run_id") else None
            result.pending_approval = bool(run.get("pending_approval"))
    elif name == "metadata":
        result.saw_terminator = True
    elif name == "replay_miss":
        result.replay_miss = True
    elif name == "error":
        body = _json(frame.data)
        message = body.get("message") if isinstance(body, dict) else None
        result.errors.append(str(message or frame.data)[:300])


def outcome_from_stream(result: StreamResult, *, mode: str | None, tokens: tuple[int, int] | None) -> RunOutcome:
    """The ``RunOutcome`` the in-process target would have produced for the same turn, as far as the wire shows."""
    steps = result.steps
    not_observable = list(_NOT_FILLED_ALWAYS)
    outcome = RunOutcome(
        text=result.response,
        tools_called=[s["tool_name"] for s in steps if s.get("tool_name")],
        tool_calls=[
            {"tool_name": s["tool_name"], "tool_input": s.get("tool_input")} for s in steps if s.get("tool_name")
        ],
        routes=_routes(steps),
        grounding=result.grounding,
        denied=_was_denied(steps),
        nodes=list(result.nodes),
    )
    parked = result.request_info or result.pending_approval or _hitl_requested(steps)
    outcome.hitl_requested = parked
    # A tool-mode specialist parks a destructive call without a step or a frame; only workflow modes announce
    # the pause, so only there is "no pause" a fact.
    if not parked and not (mode or "").startswith("workflow:"):
        not_observable.append("hitl")
    if result.grounding is None:
        not_observable.append("grounding")
    if tokens is None:
        not_observable.append("tokens")
    else:
        outcome.tokens_in, outcome.tokens_out = tokens
    outcome.not_observable = not_observable
    if result.replay_miss:
        outcome.fixture_missing = True
        outcome.error = "replay miss: no recorded model response matched this request"
    elif result.errors:
        outcome.error = f"the stack reported an error: {result.errors[0]}"
    return outcome


# ───────────────────────────── Accounts ─────────────────────────────


@dataclass(frozen=True)
class Account:
    email: str
    password: str
    role: str | None = None


def resolve_account(*, user_email: str | None, user_role: str | None, env: dict[str, str] | None = None) -> Account:
    """Who a case logs in as.

    A case ``user_email`` wins; else its ``user_role`` picks that role's seeded account; else the default user
    (``EVAL_USER_EMAIL`` / ``EVAL_USER_PASSWORD``, falling back to the seeded customer). Per-role overrides are
    ``EVAL_USER_<ROLE>_EMAIL`` / ``EVAL_USER_<ROLE>_PASSWORD``; staff passwords follow ``EVAL_STAFF_PASSWORD``
    (or the seeder's ``SEED_STAFF_PASSWORD``) when set. A password is never printed or recorded.
    """
    env = dict(os.environ) if env is None else env
    role = (user_role or "").strip() or None

    def password_for(email: str, account_role: str | None) -> str:
        explicit = env.get("EVAL_USER_PASSWORD") if email == env.get("EVAL_USER_EMAIL") else None
        if explicit:
            return explicit
        staff = env.get("EVAL_STAFF_PASSWORD") or env.get("SEED_STAFF_PASSWORD")
        if staff and (account_role in STAFF_ROLES or email.split("@")[0] in {"admin.demo", "support.demo"}):
            return staff
        if email in SEEDED_PASSWORDS:
            return SEEDED_PASSWORDS[email]
        fallback = env.get("EVAL_USER_PASSWORD")
        if fallback:
            return fallback
        raise HttpTargetError(
            f"no password known for {email}: not a seeded demo account; set EVAL_USER_EMAIL and EVAL_USER_PASSWORD"
        )

    if user_email:
        return Account(user_email, password_for(user_email, role), role)
    if role:
        key = role.upper()
        email = env.get(f"EVAL_USER_{key}_EMAIL") or ROLE_ACCOUNTS.get(role)
        if not email:
            raise HttpTargetError(f"no seeded account for role {role!r}; set EVAL_USER_{key}_EMAIL / _PASSWORD")
        override = env.get(f"EVAL_USER_{key}_PASSWORD")
        return Account(email, override or password_for(email, role), role)
    email = env.get("EVAL_USER_EMAIL") or ROLE_ACCOUNTS[DEFAULT_ROLE]
    return Account(email, password_for(email, DEFAULT_ROLE), None)


# ───────────────────────────── The runner ─────────────────────────────


@dataclass
class _Session:
    token: str
    refresh: str | None
    role: str | None


class HttpRunner:
    """Runs cases against a running orchestrator. Drop-in for ``ProductionRunner`` in ``AgentEvaluator``."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        env: dict[str, str] | None = None,
    ) -> None:
        self.base_url = normalise_base_url(base_url)
        self.timeout_s = timeout_s
        self._env = env
        self._sleep = sleep
        self._sessions: dict[str, _Session] = {}
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            transport=transport,
            timeout=httpx.Timeout(connect=10.0, read=timeout_s, write=30.0, pool=10.0),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def run(
        self,
        user_input: str,
        *,
        user_role: str | None = None,
        user_email: str | None = None,
        mode: str | None = None,
    ) -> RunOutcome:
        """One chat turn as one case. Any transport or login failure is an ``error`` outcome, not an exception."""
        try:
            account = resolve_account(user_email=user_email, user_role=user_role, env=self._env)
            session = await self._session(account)
            if user_role and session.role and session.role != user_role:
                raise HttpTargetError(
                    f"the case runs as role {user_role!r} but {account.email} has role {session.role!r}"
                )
            async with asyncio.timeout(self.timeout_s * 2):
                result, partial_error = await self._stream(account, session, user_input, mode)
        except HttpTargetError as exc:
            return RunOutcome(text="", error=str(exc), not_observable=list(_NOT_FILLED_ALWAYS))
        except TimeoutError:
            return RunOutcome(
                text="",
                error=f"the turn took longer than {self.timeout_s * 2:.0f}s",
                not_observable=list(_NOT_FILLED_ALWAYS),
            )

        tokens = await self._tokens(account, result.run_id) if result.run_id else None
        outcome = outcome_from_stream(result, mode=mode, tokens=tokens)
        if outcome.error:
            return outcome
        if partial_error:
            outcome.error = partial_error
        elif result.frames == 0:
            outcome.error = "empty stream: the orchestrator answered 200 and sent no frames"
        elif not result.saw_terminator:
            outcome.error = f"stream ended without a terminator after {result.frames} frame(s): the connection dropped"
        elif not outcome.text.strip() and not result.steps and not result.nodes:
            outcome.error = "empty response: the stream completed with no text, no tool calls and no nodes"
        return outcome

    # -- auth ---------------------------------------------------------------

    async def _session(self, account: Account) -> _Session:
        cached = self._sessions.get(account.email)
        if cached is not None:
            return cached
        return await self._login(account)

    async def _login(self, account: Account) -> _Session:
        response = await self._with_backoff(
            lambda: self._client.post("/api/auth/login", json={"email": account.email, "password": account.password}),
            what="login",
        )
        if response.status_code == 401:
            raise HttpTargetError(
                f"login as {account.email} was refused (401): is the stack seeded, and are "
                "EVAL_USER_EMAIL / EVAL_USER_PASSWORD right?"
            )
        if response.status_code != 200:
            raise HttpTargetError(f"login as {account.email} failed: HTTP {response.status_code}")
        body = response.json()
        user = body.get("user") or {}
        session = _Session(body["access_token"], body.get("refresh_token"), user.get("role"))
        self._sessions[account.email] = session
        return session

    async def _refresh(self, account: Account, session: _Session) -> _Session:
        """A fresh access token: the refresh token first, a new login if that is refused."""
        self._sessions.pop(account.email, None)
        if session.refresh:
            response = await self._with_backoff(
                lambda: self._client.post("/api/auth/refresh", json={"refresh_token": session.refresh}),
                what="token refresh",
            )
            if response.status_code == 200:
                body = response.json()
                fresh = _Session(body["access_token"], body.get("refresh_token") or session.refresh, session.role)
                self._sessions[account.email] = fresh
                return fresh
        return await self._login(account)

    # -- the turn -----------------------------------------------------------

    async def _stream(
        self, account: Account, session: _Session, message: str, mode: str | None
    ) -> tuple[StreamResult, str | None]:
        payload: dict[str, Any] = {"message": message}
        if mode:
            payload["mode"] = mode
        refreshed = False
        attempt = 0
        while True:
            result = StreamResult()
            parser = SseParser()
            try:
                async with self._client.stream(
                    "POST",
                    "/api/chat/stream",
                    json=payload,
                    headers={"Authorization": f"Bearer {session.token}", "Accept": "text/event-stream"},
                ) as response:
                    if response.status_code == 401 and not refreshed:
                        refreshed = True
                        session = await self._refresh(account, session)
                        continue
                    if response.status_code == 429:
                        attempt += 1
                        if attempt > MAX_RATE_LIMIT_RETRIES:
                            raise HttpTargetError("rate limited (429) after the bounded retries; lower the pace")
                        await self._sleep(_backoff_seconds(response.headers.get("retry-after"), attempt))
                        continue
                    if response.status_code != 200:
                        detail = (await response.aread()).decode("utf-8", "replace")[:200]
                        raise HttpTargetError(f"POST /api/chat/stream answered HTTP {response.status_code}: {detail}")
                    try:
                        async for chunk in response.aiter_text():
                            for frame in parser.feed(chunk):
                                absorb(result, frame)
                    except (httpx.TransportError, httpx.StreamError) as exc:
                        for frame in parser.finish():
                            absorb(result, frame)
                        return result, f"stream dropped after {result.frames} frame(s): {type(exc).__name__}"
                    for frame in parser.finish():
                        absorb(result, frame)
                    return result, None
            except httpx.TransportError as exc:
                raise HttpTargetError(f"cannot reach {self.base_url}: {type(exc).__name__}: {exc}") from exc

    async def _tokens(self, account: Account, run_id: str) -> tuple[int, int] | None:
        """This run's token counts from ``GET /api/runs`` (best effort: ``None`` when it is not listed)."""
        session = self._sessions.get(account.email)
        if session is None:
            return None
        try:
            response = await self._client.get(
                "/api/runs", params={"limit": 20}, headers={"Authorization": f"Bearer {session.token}"}
            )
            if response.status_code != 200:
                return None
            for entry in response.json().get("entries", []):
                if str(entry.get("id")) == run_id:
                    return int(entry.get("tokens_in") or 0), int(entry.get("tokens_out") or 0)
        except (httpx.HTTPError, ValueError, TypeError):
            logger.debug("could not read tokens for run %s", run_id)
        return None

    async def _with_backoff(self, send: Callable[[], Awaitable[httpx.Response]], *, what: str) -> httpx.Response:
        for attempt in range(1, MAX_RATE_LIMIT_RETRIES + 2):
            try:
                response = await send()
            except httpx.TransportError as exc:
                raise HttpTargetError(f"cannot reach {self.base_url} for {what}: {type(exc).__name__}: {exc}") from exc
            if response.status_code != 429:
                return response
            if attempt > MAX_RATE_LIMIT_RETRIES:
                break
            await self._sleep(_backoff_seconds(response.headers.get("retry-after"), attempt))
        raise HttpTargetError(f"{what} was rate limited (429) after the bounded retries")


def _backoff_seconds(retry_after: str | None, attempt: int) -> float:
    """Seconds before retry ``attempt``: ``evals/live_limits.py``'s jittered, capped backoff, with the server's
    ``Retry-After`` (seconds or an HTTP date) as a floor. Always bounded by ``MAX_BACKOFF_S``."""
    from evals.live_limits import RetryPolicy, backoff_delay, parse_retry_after

    policy = RetryPolicy(max_attempts=MAX_RATE_LIMIT_RETRIES + 1, base_delay=1.0, max_delay=MAX_BACKOFF_S)
    return backoff_delay(attempt, policy, retry_after=parse_retry_after(retry_after))


# ───────────────────────────── CLI wiring ─────────────────────────────


@dataclass(frozen=True)
class HttpTargetOptions:
    base_url: str
    stack: str = "unknown"
    chat_provider: str | None = None
    chat_model: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S


def normalise_base_url(raw: str) -> str:
    parts = urlsplit(raw.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"--base-url must be an http(s) URL, got {raw!r}")
    return f"{parts.scheme}://{parts.netloc.rsplit('@', 1)[-1]}".rstrip("/")


def endpoint_label(base_url: str) -> str:
    """Host and port only: what goes in ``Setup.endpoint``. Never a credential, a path or a query."""
    return urlsplit(normalise_base_url(base_url)).netloc


def add_arguments(parser: Any) -> None:
    parser.add_argument(
        "--target",
        choices=["inproc", "http"],
        default="inproc",
        help="inproc (default): call the agents in this process. http: drive a running orchestrator over "
        "POST /api/chat/stream (needs --base-url; no database or model key in this process)",
    )
    parser.add_argument("--base-url", help="Orchestrator base URL for --target http, e.g. http://localhost:8080")
    parser.add_argument(
        "--stack",
        default=os.environ.get("EVAL_STACK", "unknown"),
        help="With --target http: which stack answers (python, dotnet, py-net, net-py, or your own label). The URL "
        "cannot say, and the label names the baseline directory (default: $EVAL_STACK or 'unknown')",
    )
    parser.add_argument(
        "--chat-provider",
        help="With --target http: the remote stack's LLM_PROVIDER for the record (default: this process's own)",
    )
    parser.add_argument(
        "--chat-model",
        help="With --target http: the remote stack's chat model for the record (default: this process's)",
    )
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"With --target http: seconds to wait for the next byte of a stream (default {DEFAULT_TIMEOUT_S:.0f})",
    )


def options_from_args(args: Any, parser: Any) -> HttpTargetOptions | None:
    if args.target != "http":
        for flag, value in (
            ("--base-url", args.base_url),
            ("--chat-provider", args.chat_provider),
            ("--chat-model", args.chat_model),
        ):
            if value:
                parser.error(f"{flag} only applies to --target http")
        return None
    if not args.base_url:
        parser.error("--target http needs --base-url")
    try:
        normalise_base_url(args.base_url)
    except ValueError as exc:
        parser.error(str(exc))
    return HttpTargetOptions(
        base_url=args.base_url,
        stack=args.stack,
        chat_provider=args.chat_provider,
        chat_model=args.chat_model,
        timeout_s=args.http_timeout,
    )


def apply_setup(setup: Setup, options: HttpTargetOptions) -> Setup:
    """``setup`` relabelled for the http target: target, stack, endpoint host, and the remote's provider and model."""
    update: dict[str, Any] = {"target": "http", "stack": options.stack, "endpoint": endpoint_label(options.base_url)}
    if options.chat_provider:
        update["chat_provider"] = options.chat_provider.strip().lower()
    if options.chat_model:
        update["chat_model"] = options.chat_model
    return setup.model_copy(update=update)


def make_runner(options: HttpTargetOptions) -> HttpRunner:
    return HttpRunner(options.base_url, timeout_s=options.timeout_s)
