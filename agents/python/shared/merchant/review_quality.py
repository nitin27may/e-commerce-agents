"""Deterministic review-quality signals and seller-response drafts (#298, flow 3).

No model, no I/O. A *signal* is a plain, explainable property of one review's text and rating; the
merchant-ops agent decides how to word its advice from them, but which reviews are flagged is decided here,
so the same review always gets the same verdict and a test can pin it.

The signals are heuristics for a seller's attention queue, not moderation decisions: nothing here hides,
removes or reports a review. A review the seller thinks is abusive is reported to platform support by the
seller; the platform has no write path from this agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from shared.guardrails.sanitize import contains_injection_markers

ABUSIVE = "abusive_language"
INSTRUCTION_LIKE = "instruction_like_text"
OFF_TOPIC = "off_topic_fulfilment"
LOW_SIGNAL = "low_signal"
RATING_MISMATCH = "rating_text_mismatch"

#: The order a review's single headline ``quality`` is chosen in: the first signal present wins.
_QUALITY_ORDER = (INSTRUCTION_LIKE, ABUSIVE, OFF_TOPIC, RATING_MISMATCH, LOW_SIGNAL)

_ABUSE = re.compile(
    r"\b(?:idiots?|morons?|stupid|scum(?:bags?)?|crooks?|thieves|thief|fraudsters?|"
    r"f+u+c+k\w*|sh[i1]t\w*|bastards?|bitch\w*|pieces?\s+of\s+(?:crap|shit)|"
    r"(?:will|going\s+to|gonna)\s+(?:sue|kill|hurt|destroy)\s+you|i\s+hope\s+you\s+(?:die|go\s+bankrupt))\b",
    re.I,
)

#: Words that say the review is about delivery or service, not the product.
_FULFILMENT = re.compile(
    r"\b(?:ship(?:ping|ped)?|deliver(?:y|ed)|courier|carrier|packag(?:e|ing)|parcel|arriv(?:ed|al)|late|"
    r"customer\s+(?:service|support)|refund|tracking|dispatch(?:ed)?)\b",
    re.I,
)
#: Words that say the review is about the thing itself.
_PRODUCT = re.compile(
    r"\b(?:quality|works?|working|worked|broke(?:n)?|battery|size|fit(?:s)?|sound|screen|material|durab\w+|"
    r"comfort\w*|design|performance|build|feature\w*|stopped|defect\w*|noise|noisy|colou?r|taste|flavou?r|"
    r"charge[sd]?|setup|install\w*|fabric|stitch\w*)\b",
    re.I,
)

_POSITIVE = re.compile(r"\b(?:love[sd]?|great|excellent|amazing|perfect|fantastic|brilliant|recommend\w*)\b", re.I)
_NEGATIVE = re.compile(
    r"\b(?:terrible|awful|horrible|useless|worst|broke(?:n)?|defective|disappoint\w*|waste|junk|rubbish)\b", re.I
)

_GENERIC = frozenset(
    {
        "good",
        "great",
        "nice",
        "ok",
        "okay",
        "fine",
        "bad",
        "love it",
        "like it",
        "5 stars",
        "five stars",
        "as described",
    }
)
_WORD = re.compile(r"[A-Za-z0-9']+")
_MIN_WORDS = 6


@dataclass(frozen=True)
class ReviewQuality:
    signals: tuple[str, ...]
    quality: str
    suggested_action: str
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "signals": list(self.signals),
            "quality": self.quality,
            "suggested_action": self.suggested_action,
            "reasons": list(self.reasons),
        }


def assess_review(*, rating: int, title: str | None, body: str | None, product_name: str = "") -> ReviewQuality:
    """Signals for one review, a headline ``quality`` and one ``suggested_action``.

    ``suggested_action`` is one of ``report_to_support`` (abusive or instruction-like text: do not engage),
    ``respond`` (a real complaint or a mixed review a polite reply helps), ``monitor`` (off-topic or
    low-signal: a reply adds nothing, keep an eye on the pattern) and ``none`` (a fine positive review).
    """
    text = f"{title or ''} {body or ''}".strip()
    words = _WORD.findall(text)
    signals: list[str] = []
    reasons: list[str] = []

    if contains_injection_markers(text):
        signals.append(INSTRUCTION_LIKE)
        reasons.append("the text contains instructions aimed at an automated reader, not feedback about the product")
    if _ABUSE.search(text):
        signals.append(ABUSIVE)
        reasons.append("the text contains insults or threats")

    name_tokens = {t.lower() for t in _WORD.findall(product_name) if len(t) > 3}
    mentions_product = bool(_PRODUCT.search(text)) or any(t in text.lower() for t in name_tokens)
    if _FULFILMENT.search(text) and not mentions_product:
        signals.append(OFF_TOPIC)
        reasons.append("it is about delivery or service and says nothing about the product itself")

    positive, negative = bool(_POSITIVE.search(text)), bool(_NEGATIVE.search(text))
    if (rating <= 2 and positive and not negative) or (rating >= 4 and negative and not positive):
        signals.append(RATING_MISMATCH)
        reasons.append(f"the {rating}-star rating points one way and the wording the other")

    lowered = " ".join(w.lower() for w in words)
    if len(words) < _MIN_WORDS or lowered in _GENERIC:
        signals.append(LOW_SIGNAL)
        reasons.append(f"fewer than {_MIN_WORDS} words, or only a generic phrase, so it says little about the product")

    quality = next((s for s in _QUALITY_ORDER if s in signals), "ok")
    if INSTRUCTION_LIKE in signals or ABUSIVE in signals:
        action = "report_to_support"
    elif OFF_TOPIC in signals or (LOW_SIGNAL in signals and rating >= 3):
        action = "monitor"
    elif rating <= 3:
        action = "respond"
    else:
        action = "none"
    return ReviewQuality(tuple(signals), quality, action, tuple(reasons))


_NAME_OK = re.compile(r"^[A-Za-z][A-Za-z'-]{0,29}$")


def _greeting(reviewer: str | None) -> str:
    first = (reviewer or "").strip().split(" ")[0]
    return f"Hello {first}," if _NAME_OK.match(first) else "Hello,"


def draft_response(*, rating: int, product_name: str, reviewer: str | None, quality: ReviewQuality) -> str | None:
    """A polite reply the seller can edit and send, or ``None`` when a reply is not advised.

    Never quotes the review, so abusive or injected text cannot be echoed back. ``None`` for text that
    carries instructions aimed at a machine (do not engage with it at all) and for a fine positive review.
    """
    if INSTRUCTION_LIKE in quality.signals or quality.suggested_action == "none":
        return None
    greeting = _greeting(reviewer)
    close = "Kind regards,\n[Your name]\n[Your shop]"
    if ABUSIVE in quality.signals:
        return (
            f"{greeting}\n\nThank you for letting us know about your experience with the {product_name}. "
            "We are sorry it fell short and we would like to understand what went wrong. "
            "Please contact our support team with your order number so we can look into it properly.\n\n" + close
        )
    if OFF_TOPIC in quality.signals:
        return (
            f"{greeting}\n\nThank you for your feedback. We are sorry about the delivery experience; we pass "
            "what we hear on to our carrier and we want to make it right. If anything is wrong with your order, "
            "please reach out to our support team with your order number.\n\n" + close
        )
    if rating <= 2:
        return (
            f"{greeting}\n\nThank you for taking the time to review the {product_name}, and we are sorry it did not "
            "meet your expectations. We take this seriously and would like the chance to put it right. Please "
            "contact our support team with your order number and we will arrange a repair, replacement or refund "
            "as appropriate.\n\n" + close
        )
    return (
        f"{greeting}\n\nThank you for your honest review of the {product_name}. We are glad to hear what worked "
        "and we are taking your comments about what did not into account. If there is anything we can do to "
        "improve your experience, please contact our support team.\n\n" + close
    )
