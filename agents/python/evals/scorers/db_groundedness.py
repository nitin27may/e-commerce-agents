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
from shared.grounding.verifier import GroundingReport, verify_claims


def score_from_report(grounding: dict[str, Any] | None) -> float:
    """Score from an already-computed report (e.g. ``RunOutcome.grounding``,
    which ``GroundingVerificationMiddleware`` already populated during the
    production run — free, no extra DB round trip).

    1.0 when there's nothing to verify: a response making no checkable
    claims isn't ungrounded, it's just not asserting anything specific.
    """
    if not grounding or grounding.get("total", 0) == 0:
        return 1.0
    return grounding["verified"] / grounding["total"]


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
