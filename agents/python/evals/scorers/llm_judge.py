"""LLM-as-judge scorer for relevance/completeness — replaces the old
keyword-alias table (``AgentEvaluator._score_completeness``), which counted
a bare ``"$"`` character as satisfying an expected ``price`` field
regardless of whether an actual price appeared.

Not for groundedness — ``db_groundedness.py``'s deterministic DB check is
strictly better whenever there's real data to check a claim against. This
is for the part that isn't mechanically checkable: does the response
actually answer the question and cover what was expected, in a way a
keyword table can't capture (a response can convey a price without the
literal word "price").

Verdicts are cached on ``(case_id, sha256(response_text))`` so re-running a
suite after an unrelated code change doesn't re-spend judge tokens on
unchanged responses.
"""

from __future__ import annotations

import hashlib
import json
import logging

from pydantic import BaseModel, Field, ValidationError

from evals.judge import JudgeConfig
from shared.prompt_loader import load_raw_prompt

logger = logging.getLogger(__name__)

_JUDGE_INSTRUCTIONS = load_raw_prompt("_eval/completeness-judge")


#: ``failure_mode`` of a verdict the judge could not give because the provider's content filter blocked the call.
JUDGE_UNAVAILABLE = "judge_unavailable"

_CONTENT_FILTER_MARKERS = ("content_filter", "responsibleaipolicyviolation", "content management policy")


def is_content_filter_error(exc: BaseException) -> bool:
    """True when ``exc`` is a provider content-filter refusal (Azure OpenAI ``content_filter``), by code or message.

    Every other error stays a ``judge_error``: only a refusal of this kind means "no verdict was possible".
    """
    code = getattr(exc, "code", None)
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        code = code or body.get("code") or (body.get("error") or {}).get("code")
    if isinstance(code, str) and code.lower() == "content_filter":
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _CONTENT_FILTER_MARKERS)


class JudgeVerdict(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str
    failure_mode: str | None = None


# Process-lifetime cache — evals run as a single short-lived CLI invocation,
# so this doesn't need persistence across runs (see baselines for that).
_cache: dict[str, JudgeVerdict] = {}


def _cache_key(case_id: str, response_text: str, judge_key: str = "") -> str:
    digest = hashlib.sha256(response_text.encode("utf-8")).hexdigest()[:16]
    return f"{judge_key}:{case_id}:{digest}"


async def judge_response(
    case_id: str,
    user_input: str,
    response_text: str,
    expected_fields: list[str],
    judge: JudgeConfig | None = None,
) -> JudgeVerdict:
    """Judge one response. ``judge`` says which model (``evals/judge.py``); None keeps the chat client."""
    key = _cache_key(case_id, response_text, judge.cache_key if judge else "")
    cached = _cache.get(key)
    if cached is not None:
        return cached

    verdict = await _call_judge(user_input, response_text, expected_fields, judge)
    _cache[key] = verdict
    return verdict


async def _call_judge(
    user_input: str, response_text: str, expected_fields: list[str], judge_config: JudgeConfig | None = None
) -> JudgeVerdict:
    from agent_framework import Agent

    from shared.factory import get_chat_client, get_judge_chat_client

    client = get_chat_client() if judge_config is None or judge_config.via_chat_client else get_judge_chat_client()
    judge = Agent(
        client=client,
        instructions=_JUDGE_INSTRUCTIONS,
        name="eval-judge",
    )
    prompt = (
        f"User question: {user_input}\n"
        f"Expected to cover: {', '.join(sorted(expected_fields)) or '(nothing specific)'}\n"
        f"Assistant response:\n{response_text or '(empty)'}"
    )
    try:
        response = await judge.run(prompt)
    except Exception as exc:
        if is_content_filter_error(exc):
            # The provider's content filter refused the *judge call* (adversarial text in the answer under
            # test), so no verdict exists. A distinct status, not a zero: it is left out of the completeness mean.
            logger.warning("llm_judge.content_filtered error=%s", exc)
            return JudgeVerdict(
                score=0.0,
                reasoning=f"judge unavailable: content filter refused the call ({exc})",
                failure_mode=JUDGE_UNAVAILABLE,
            )
        logger.warning("llm_judge.call_failed error=%s", exc)
        return JudgeVerdict(score=0.0, reasoning=f"judge call failed: {exc}", failure_mode="judge_error")

    return _parse_verdict(response.text or "")


def _parse_verdict(text: str) -> JudgeVerdict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    try:
        data = json.loads(stripped)
        return JudgeVerdict(**data)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        logger.warning("llm_judge.unparsable_response error=%s text=%s", exc, text[:200])
        return JudgeVerdict(score=0.0, reasoning="judge response unparsable", failure_mode="judge_error")
