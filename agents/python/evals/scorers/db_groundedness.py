"""Deterministic groundedness scorer, built on Phase 2's grounding verifier.

Replaces the old ``AgentEvaluator._score_groundedness`` (evaluator.py),
which returned 1.0 whenever any tool was called — never comparing response
content to what the tool actually returned, so a fabricated price scored
identically to a real one. Score here = ``verified_claims / total_claims``,
computed by ``shared/grounding/verifier.py``'s same three-tier check
(ledger match -> batched DB match -> consistency) production traffic uses.

Inherits that verifier's own documented limitation: this checks whether a
claim is real (not fabricated), not whether it's authorized for this user —
grounding and authorization are different questions.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from shared.grounding.extractor import extract_claims
from shared.grounding.ledger import GroundingLedger
from shared.grounding.verifier import GROUNDED_STATUSES, GroundingReport, user_supplied_values, verify_claims

# Verdict types the verifier can only settle against the ledger of the process that ran it.
_LEDGER_ONLY_CLAIM_TYPES = frozenset({"amount", "tracking"})


def score_from_report(grounding: dict[str, Any] | None, *, ledger_complete: bool = True) -> float:
    """Score from an already-computed report (e.g. ``RunOutcome.grounding``,
    which ``GroundingVerificationMiddleware`` already populated during the
    production run — free, no extra DB round trip).

    1.0 when there's nothing to verify: a response making no checkable
    claims isn't ungrounded, it's just not asserting anything specific.

    ``ledger_complete=False`` is for a run whose ledger cannot hold the facts behind the answer: the
    orchestrator relays specialist text, and a specialist's tool results never cross the A2A hop, so
    an amount or tracking number it quotes is "unverifiable" by construction, not wrong. Those
    verdicts are left out of the score (not applicable). Id and card claims still count: they are
    checked against the database, and ``not_found`` / ``price_mismatch`` are real failures.
    """
    if not grounding or grounding.get("total", 0) == 0:
        return 1.0
    if ledger_complete or not grounding.get("claims"):
        return grounding["verified"] / grounding["total"]
    counted = [
        c
        for c in grounding["claims"]
        if not (c.get("status") == "unverifiable" and c.get("type") in _LEDGER_ONLY_CLAIM_TYPES)
    ]
    if not counted:
        return 1.0
    return sum(1 for c in counted if c.get("status") in GROUNDED_STATUSES) / len(counted)


def reclassify_user_supplied(grounding: dict[str, Any] | None, user_texts: list[Any]) -> dict[str, Any] | None:
    """A stored report with each unsettled amount or bare-id claim the caller supplied relabelled ``user_supplied``.

    For records written before the verifier knew about user-supplied values: ``user_texts`` is the case's
    query and the tool-call arguments. Only a value that appears in them changes, and only from
    ``unverifiable`` / ``not_found``; a figure nobody supplied keeps its verdict. Idempotent.
    """
    if not grounding or not grounding.get("claims"):
        return grounding
    amounts: set[float] = set()
    ids: set[str] = set()
    for text in user_texts:
        a, i = user_supplied_values(text)
        amounts |= a
        ids |= i
    changed = False
    claims = []
    for claim in grounding["claims"]:
        if claim.get("status") in ("unverifiable", "not_found") and _supplied(claim, amounts, ids):
            claim = {**claim, "status": "user_supplied", "detail": "supplied in the user's message or tool arguments"}
            changed = True
        claims.append(claim)
    if not changed:
        return grounding
    verified = sum(1 for c in claims if c.get("status") in GROUNDED_STATUSES)
    return {
        **grounding,
        "claims": claims,
        "verified": verified,
        "unverified": len(claims) - verified,
        "user_supplied": sum(1 for c in claims if c.get("status") == "user_supplied"),
    }


def _supplied(claim: dict[str, Any], amounts: set[float], ids: set[str]) -> bool:
    identifier = str(claim.get("id", ""))
    if claim.get("type") == "bare_id":
        return identifier.lower() in ids
    if claim.get("type") == "amount":
        try:
            value = float(identifier.lstrip("$"))
        except ValueError:
            return False
        return any(abs(value - a) < 0.01 for a in amounts)
    return False


async def score_groundedness(
    response_text: str,
    pool: asyncpg.Pool | None,
    ledger: GroundingLedger | None = None,
) -> tuple[float, dict[str, Any]]:
    """From-scratch groundedness check — use when a report wasn't already
    computed (e.g. ``GROUNDING_MODE=off`` in the eval environment, or
    scoring arbitrary text that didn't go through the middleware).
    """
    claims = extract_claims(response_text)
    if claims.total_count == 0:
        return 1.0, _report_dict(GroundingReport())

    report = await verify_claims(claims, ledger, pool)
    score = report.verified_count / report.total_count if report.total_count else 1.0
    return score, _report_dict(report)


def _report_dict(report: GroundingReport) -> dict[str, Any]:
    return {
        "total": report.total_count,
        "verified": report.verified_count,
        "unverified": report.unverified_count,
        "user_supplied": report.user_supplied_count,
        "derived": report.derived_count,
        "claims": [
            {
                "type": v.claim_type,
                "id": v.identifier,
                "status": v.status,
                "detail": v.detail,
                "source": v.source,
            }
            for v in report.verdicts
        ],
    }
