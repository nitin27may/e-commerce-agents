"""Coarse local content-moderation classifier for outbound agent text.

Distinct from ``shared/guardrails/sanitize.py``: sanitization defangs
adversarial *instructions* hiding inside untrusted input (a review body
telling the model to "ignore previous instructions") so they never
influence the next turn. This module classifies the model's own *output*
text against content-policy categories (self-harm, violence, hate/
harassment, sexual content) — a different problem. An agent that
correctly resists every injection attempt can still generate harmful text
on its own, especially once handoff/group-chat modes let agents draft
free-form prose (a seller response, a review summary) rather than just
relaying tool data.

Pure functions: no I/O, no LLM call, no external API. Deliberately a
small set of high-precision phrase patterns, mirroring
``sanitize.py``'s own stated philosophy (low false-positive over
exhaustive recall) — this is a coarse first-pass filter, not a trained
classifier, and will miss anything phrased less directly than these
patterns. Documented as a real limitation, not glossed over: see
``docs/concepts/10-guardrails.md`` for the layered-defense framing this
is one honestly-scoped layer of.
"""

from __future__ import annotations

import re
from enum import StrEnum


class ModerationCategory(StrEnum):
    SELF_HARM = "self_harm"
    VIOLENCE = "violence"
    HATE_HARASSMENT = "hate_harassment"
    SEXUAL = "sexual"


_PATTERNS: dict[ModerationCategory, tuple[re.Pattern[str], ...]] = {
    ModerationCategory.SELF_HARM: (
        re.compile(r"\bkill\s+(?:myself|yourself)\b", re.I),
        re.compile(r"\b(?:commit|committing)\s+suicide\b", re.I),
        re.compile(r"\bways?\s+to\s+(?:end|take)\s+(?:my|your|his|her|their)\s+(?:own\s+)?life\b", re.I),
        re.compile(r"\bself[\s-]harm\b", re.I),
    ),
    ModerationCategory.VIOLENCE: (
        re.compile(r"\bhow\s+to\s+(?:build|make)\s+a\s+(?:bomb|explosive|weapon)\b", re.I),
        re.compile(r"\bi\s+(?:will|'ll|am\s+going\s+to)\s+kill\s+you\b", re.I),
        re.compile(r"\bmass\s+shooting\b", re.I),
    ),
    ModerationCategory.HATE_HARASSMENT: (
        re.compile(r"\ball\s+\w+\s+(?:people\s+)?(?:are|should\s+be)\s+(?:killed|exterminated|eliminated)\b", re.I),
        re.compile(r"\byou'?re\s+(?:a\s+)?(?:worthless|subhuman)\b", re.I),
    ),
    ModerationCategory.SEXUAL: (
        re.compile(r"\bsexually\s+explicit\s+(?:content|description|story)\b", re.I),
        re.compile(r"\bchild\s+sexual\s+abuse\b", re.I),
    ),
}


def classify(text: str) -> set[ModerationCategory]:
    """Return every category whose patterns match ``text``. Empty set = clean."""
    hits: set[ModerationCategory] = set()
    for category, patterns in _PATTERNS.items():
        if any(p.search(text) for p in patterns):
            hits.add(category)
    return hits
