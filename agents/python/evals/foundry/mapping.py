"""Map run records onto Foundry evaluator inputs, and say what each evaluator cannot score.

A ``RunRecord`` already uses Foundry's input names (``query``, ``response``, ``context``, ``tool_calls``,
``ground_truth``), so the mapping is mostly a rename-free copy. What differs is what each evaluator
*requires* (verified against ``azure-ai-evaluation`` 1.18.x, the overloads of each ``__call__``):

=====================  =========================================================  ===========================
Evaluator              Call inputs                                                 What our records lack
=====================  =========================================================  ===========================
GroundednessEvaluator  response, context, query (optional)                         ``context`` is the grounding
                                                                                   report's claim lines, not
                                                                                   the raw tool output
IntentResolution       query, response, tool_definitions (optional)                nothing required
ToolCallAccuracy       query, tool_definitions (required), tool_calls | response   tool definitions (supply
                                                                                   ``--tool-definitions``);
                                                                                   tool results
TaskAdherence          query, response, tool_definitions (optional)                the agent's system prompt
                                                                                   (Foundry wants it as the
                                                                                   first query message)
IndirectAttack         query, response (service-based; needs an Azure AI project)  the injected document
=====================  =========================================================  ===========================

Shapes (verified against the installed ``azure-ai-evaluation`` 1.18.12, ``_common/utils.py`` and the
validators under ``_evaluators/_common/_validators``): the SDK accepts a plain string for ``query`` and
``response``, but ``IntentResolution`` and ``TaskAdherence`` then run ``reformat_conversation_history`` /
``reformat_agent_response``, which only understand message lists. Given a string they log "could not be
parsed; falling back to raw input" and the judge sees an unstructured prompt, which degrades the score without
failing the call. So those two get message lists::

    query    [{"role": "user", "content": [{"type": "text", "text": "..."}]}]
    response [{"role": "assistant", "content": [{"type": "tool_call", "tool_call_id": "call_0",
                                                  "name": "...", "arguments": {...}}]},
              {"role": "assistant", "content": [{"type": "text", "text": "..."}]}]

The tool-call message comes before the final text, because a response ending in a tool call is treated as
"intermediate" and not scored. ``ToolCallAccuracy`` takes ``tool_calls`` as the same flat ``tool_call`` items
and ``tool_definitions`` as ``{name, description, parameters}`` with ``parameters`` a dict, one for every tool
called. Groundedness handles strings and keeps them.

``inputs_for`` returns either the keyword arguments to call the evaluator with, or the reason this record
cannot be scored by it. A skipped record is reported as skipped, never as a zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evals.records import RunRecord

GROUNDEDNESS = "groundedness"
INTENT_RESOLUTION = "intent_resolution"
TOOL_CALL_ACCURACY = "tool_call_accuracy"
TASK_ADHERENCE = "task_adherence"
INDIRECT_ATTACK = "indirect_attack"

EVALUATOR_NAMES: tuple[str, ...] = (
    GROUNDEDNESS,
    INTENT_RESOLUTION,
    TOOL_CALL_ACCURACY,
    TASK_ADHERENCE,
    INDIRECT_ATTACK,
)

# SDK class name behind each short name.
SDK_CLASSES: dict[str, str] = {
    GROUNDEDNESS: "GroundednessEvaluator",
    INTENT_RESOLUTION: "IntentResolutionEvaluator",
    TOOL_CALL_ACCURACY: "ToolCallAccuracyEvaluator",
    TASK_ADHERENCE: "TaskAdherenceEvaluator",
    INDIRECT_ATTACK: "IndirectAttackEvaluator",
}

# Evaluators that call a judge model you configure (model_config), and the one that calls the Foundry
# safety service instead (azure_ai_project + credential).
JUDGE_EVALUATORS: tuple[str, ...] = (GROUNDEDNESS, INTENT_RESOLUTION, TOOL_CALL_ACCURACY, TASK_ADHERENCE)
SERVICE_EVALUATORS: tuple[str, ...] = (INDIRECT_ATTACK,)

# What an evaluator cannot score from our records, in words (rendered into the docs and the run summary).
LIMITATIONS: dict[str, str] = {
    GROUNDEDNESS: (
        "context is the grounding report's claim lines (type, id, status, source), not the raw tool output, so "
        "the judge checks the answer against our verifier's findings rather than against the data itself; "
        "records with no grounding report (GROUNDING_MODE=off) are skipped"
    ),
    INTENT_RESOLUTION: "no tool definitions are passed unless supplied; the judge sees the query and the final answer",
    TOOL_CALL_ACCURACY: (
        "needs tool definitions, which records do not carry (pass --tool-definitions FILE, or 'auto' to read them "
        "off the agents); a record calling a tool with no definition is skipped; tool results are not "
        "captured by the in-process target, so retries and errors are invisible; skipped when no tool was called"
    ),
    TASK_ADHERENCE: (
        "records do not carry the agent's system prompt, which Foundry reads as the first query message, so rule "
        "and procedure adherence is judged without the rules"
    ),
    INDIRECT_ATTACK: (
        "a Foundry safety-service call (needs AZURE_AI_PROJECT and Azure credentials, not a judge deployment); "
        "scored on safety records only; it classifies the response, and the injected document is not in the record"
    ),
}


@dataclass(frozen=True)
class Mapped:
    """The result of mapping one record for one evaluator: inputs to call with, or a reason to skip."""

    inputs: dict[str, Any] | None
    skip_reason: str = ""


def tool_calls_for_foundry(record: RunRecord) -> list[dict[str, Any]]:
    """The record's tool calls in Foundry's ``tool_calls`` shape.

    A ``tool_call_id`` is added (``call_<n>``) because Foundry's documented examples carry one and records do
    not store the model's own ids.
    """
    return [
        {
            "type": "tool_call",
            "tool_call_id": f"call_{index}",
            "name": call.name,
            "arguments": call.arguments
            if isinstance(call.arguments, dict)
            else ({} if call.arguments is None else {"value": call.arguments}),
        }
        for index, call in enumerate(record.tool_calls)
    ]


def query_messages(record: RunRecord) -> list[dict[str, Any]]:
    """The record's query as a Foundry message list (one user message)."""
    return [{"role": "user", "content": [{"type": "text", "text": record.query}]}]


def response_messages(record: RunRecord) -> list[dict[str, Any]]:
    """The record's response as a Foundry message list: its tool calls (if any), then the final answer.

    Tool results are not in a record, so no ``tool`` messages are emitted. The answer comes last: a response
    that ends in a tool call is "intermediate" to the SDK and is not scored.
    """
    messages: list[dict[str, Any]] = []
    calls = tool_calls_for_foundry(record)
    if calls:
        messages.append({"role": "assistant", "content": calls})
    messages.append({"role": "assistant", "content": [{"type": "text", "text": record.response}]})
    return messages


def normalize_tool_definitions(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool definitions in Foundry's ``{name, description, parameters}`` shape.

    Accepts that shape as is and the OpenAI function spec (``{"type": "function", "function": {...}}``) that
    ``to_json_schema_spec()`` returns. ``parameters`` is always a dict: the SDK validator refuses a definition
    without one.
    """
    out: list[dict[str, Any]] = []
    for item in raw:
        spec = item["function"] if isinstance(item.get("function"), dict) else item
        parameters = spec.get("parameters")
        out.append(
            {
                "name": str(spec.get("name", "")),
                "description": str(spec.get("description", "")),
                "parameters": parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}},
            }
        )
    return out


def inputs_for(
    evaluator: str,
    record: RunRecord,
    *,
    tool_definitions: list[dict[str, Any]] | None = None,
) -> Mapped:
    """Inputs for ``evaluator`` from ``record``, or why the record cannot be scored by it."""
    if evaluator not in EVALUATOR_NAMES:
        raise ValueError(f"unknown evaluator {evaluator!r}; known: {', '.join(EVALUATOR_NAMES)}")
    if record.error or record.fixture_missing:
        return Mapped(None, "the run itself failed (error or missing fixture); there is no answer to score")
    if not record.response.strip():
        return Mapped(None, "empty response")
    if not record.query.strip():
        return Mapped(None, "empty query")

    if evaluator == GROUNDEDNESS:
        if not record.context.strip():
            return Mapped(None, "no context: the record has no grounding report")
        return Mapped({"query": record.query, "response": record.response, "context": record.context})
    definitions = normalize_tool_definitions(tool_definitions) if tool_definitions else None
    if evaluator in (INTENT_RESOLUTION, TASK_ADHERENCE):
        inputs: dict[str, Any] = {"query": query_messages(record), "response": response_messages(record)}
        if definitions:
            inputs["tool_definitions"] = definitions
        return Mapped(inputs)
    if evaluator == TOOL_CALL_ACCURACY:
        if not record.tool_calls:
            return Mapped(None, "no tool calls in the record")
        if not definitions:
            return Mapped(None, "no tool definitions supplied (--tool-definitions)")
        missing = sorted({call.name for call in record.tool_calls} - {d["name"] for d in definitions})
        if missing:
            return Mapped(None, f"no tool definition for: {', '.join(missing)}")
        return Mapped(
            {
                "query": query_messages(record),
                "tool_calls": tool_calls_for_foundry(record),
                "tool_definitions": definitions,
            }
        )
    # INDIRECT_ATTACK
    if record.suite != "safety":
        return Mapped(None, "scored on safety records only")
    return Mapped({"query": record.query, "response": record.response})
