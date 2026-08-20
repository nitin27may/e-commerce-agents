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

logger = logging.getLogger(__name__)

_JUDGE_INSTRUCTIONS = (
    "You are grading an AI shopping assistant's response for relevance and completeness. "
    "Given the user's question, the fields it was expected to cover, and the assistant's "
    "actual response, judge substance, not phrasing — a response that conveys a price "
    "without the literal word 'price' still counts. "
    "Reply with ONLY a JSON object, no other text: "
    '{"score": <float 0.0-1.0>, "reasoning": "<one sentence>", "failure_mode": <string or null>}. '
    "score 1.0 = fully relevant and complete, 0.5 = partially answers or misses some expected "
    "content, 0.0 = irrelevant or empty. failure_mode is a short label when score < 1.0 "
    "(e.g. \"missing_field\", \"off_topic\", \"empty_response\"), else null."
)


class JudgeVerdict(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str
    failure_mode: str | None = None


# Process-lifetime cache — evals run as a single short-lived CLI invocation,
# so this doesn't need persistence across runs (see baselines for that).
_cache: dict[str, JudgeVerdict] = {}


def _cache_key(case_id: str, response_text: str) -> str:
    digest = hashlib.sha256(response_text.encode("utf-8")).hexdigest()[:16]
    return f"{case_id}:{digest}"


async def judge_response(
    case_id: str,
    user_input: str,
    response_text: str,
    expected_fields: list[str],
) -> JudgeVerdict:
    key = _cache_key(case_id, response_text)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    verdict = await _call_judge(user_input, response_text, expected_fields)
    _cache[key] = verdict
    return verdict


async def _call_judge(user_input: str, response_text: str, expected_fields: list[str]) -> JudgeVerdict:
    from agent_framework import Agent

    from shared.factory import get_chat_client

    judge = Agent(
        client=get_chat_client(),
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
