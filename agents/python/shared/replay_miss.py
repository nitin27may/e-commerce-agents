"""How a replay miss travels between processes (#86).

Under ``LLM_PROVIDER=replay`` a prompt with no recording raises
:class:`~shared.replay_client.ReplayFixtureMissingError` inside whichever process
made the model call. When that process is the orchestrator the chat route catches
it. When it is a *specialist*, the miss has to cross an A2A hop and arrive at the
browser as the same thing, not as an apology or as tool-result text the
orchestrator's model would then answer (and miss on again).

The wire contract, identical for every consumer:

* ``POST /message:send``    -> HTTP 424 with body ``{"error": "replay_miss"}``
* ``POST /message:stream``  -> an ``event: replay_miss`` frame (the 200 is already
  sent), then ``[DONE]``

Nothing about the missed request (hash, prompt, fixture path) is on the wire or in
a log line: the user-facing text is the tour manifest's ``miss_message`` and the
exception's own text is never forwarded.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from agent_framework import FunctionInvocationContext, FunctionMiddleware, MiddlewareTermination

from shared.replay_client import ReplayFixtureMissingError

logger = logging.getLogger(__name__)

REPLAY_MISS_EVENT = "replay_miss"
REPLAY_MISS_STATUS = 424
REPLAY_MISS_BODY: dict[str, str] = {"error": "replay_miss"}

# Used when no tour manifest is configured (a replay run that is not the demo).
DEFAULT_MISS_MESSAGE = (
    "This demo replays recorded model responses, and this prompt was not recorded. "
    "Try one of the suggested prompts instead."
)


class SpecialistReplayMissError(ReplayFixtureMissingError):
    """A specialist (another process) reported a replay miss.

    Subclasses the local miss error so every ``except ReplayFixtureMissingError``
    handles both. The message is fixed on purpose: it never carries the specialist's
    text.
    """

    def __init__(self) -> None:
        super().__init__("a specialist agent has no recorded response for this request")


def miss_message() -> str:
    """The text to show a user whose prompt missed.

    Reads the configured tour manifest when there is one; a broken or absent
    manifest must never turn a miss into a different failure.
    """
    try:
        from shared.demo_manifest import load_manifest

        manifest = load_manifest()
    except Exception:
        logger.warning("replay_miss.manifest_unavailable")
        return DEFAULT_MISS_MESSAGE
    return manifest.miss_message if manifest is not None else DEFAULT_MISS_MESSAGE


def miss_payload() -> dict[str, str]:
    """The structured body of a miss: the same shape on the SSE frame and the blocking response."""
    return {"message": miss_message()}


def replay_mode_active() -> bool:
    """Whether THIS process's own model provider is ``replay``.

    The one gate every miss signal passes through. A 424 or a ``replay_miss`` frame means "no recording" only
    to a process that is itself replaying: elsewhere a proxy's 424, or a misconfigured or hostile specialist,
    would make a production orchestrator delete the user's message, drop the answer and show the demo text.
    Outside replay mode those signals are an ordinary specialist failure.
    """
    from shared.config import settings

    # Normalised exactly as ``Settings`` and ``get_chat_client`` do (trimmed, lower-cased), so the gate and the
    # client the process actually builds cannot disagree about whether it is replaying. .NET: ``ReplayMiss.IsActive``.
    return settings.LLM_PROVIDER.strip().lower() == "replay"


def is_replay_miss_status(status_code: int) -> bool:
    """True for a miss signalled by HTTP status -- only when this process is replaying (:func:`replay_mode_active`)."""
    return status_code == REPLAY_MISS_STATUS and replay_mode_active()


_MISS_ERROR_TYPES = frozenset({ReplayFixtureMissingError.__name__, SpecialistReplayMissError.__name__})


def is_replay_miss_event(payload: Any) -> bool:
    """True for the ``error`` event a workflow emits when an executor died of a miss.

    A MAF workflow reports a failed executor as an event (whose message names the
    fixture hash) before it re-raises; the chat route uses this to keep that text
    off the wire and let the raised miss become the one frame the user sees.
    Never true outside replay mode.
    """
    if not replay_mode_active():
        return False
    data = payload.get("data") if isinstance(payload, dict) else None
    return isinstance(data, dict) and data.get("error_type") in _MISS_ERROR_TYPES


class _MissBox:
    def __init__(self) -> None:
        self.missed = False


# A mutable box in a ContextVar, for the reason shared/run_usage.py gives: the tool call runs in a
# task MAF spawns, and a plain `set()` there is invisible to the parent. The box is created in the
# parent first, so the child's write to it is seen.
_miss_box: ContextVar[_MissBox | None] = ContextVar("replay_miss_box", default=None)


def begin_miss_tracking() -> None:
    """Start watching this agent run for a specialist's miss (call before ``agent.run``)."""
    _miss_box.set(_MissBox())


def raise_if_specialist_missed() -> None:
    """Raise :class:`SpecialistReplayMissError` if a specialist's miss ended this run."""
    box = _miss_box.get()
    if box is not None and box.missed:
        raise SpecialistReplayMissError()


class _SpecialistMissFunctionMiddleware(FunctionMiddleware):
    """Ends the tool loop on a specialist's miss instead of letting the model answer it.

    MAF turns any exception a tool raises into a result the model reads and calls the model
    again. A miss is not something the model can recover from, and that second call is exactly
    what must not happen, so this records the miss, then terminates the loop (``MiddlewareTermination``
    ends it without another model call). ``raise_if_specialist_missed`` then turns the recorded
    miss back into the exception the chat route reports.
    """

    async def process(self, context: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]) -> None:
        try:
            await call_next()
        except ReplayFixtureMissingError:
            box = _miss_box.get()
            if box is None:
                raise
            box.missed = True
            raise MiddlewareTermination(result="[no recorded response]") from None


def replay_miss_middleware() -> list[Any]:
    """The middleware that ends a run on a specialist's miss, or nothing outside replay mode.

    Returning ``[]`` for every other provider is what keeps this change from touching them.
    """
    if not replay_mode_active():
        return []
    return [_SpecialistMissFunctionMiddleware()]
