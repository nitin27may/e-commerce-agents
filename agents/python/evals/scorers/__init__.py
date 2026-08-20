"""Eval scorers.

- ``db_groundedness`` — deterministic, reuses ``shared/grounding/verifier.py``.
  Zero LLM cost, CI-safe. Prefer ``score_from_report()`` when a
  ``RunOutcome.grounding`` report is already available (the production
  middleware already computed it); fall back to ``score_groundedness()``
  for a from-scratch check.
- ``llm_judge`` — for relevance/completeness, the part that isn't
  mechanically checkable against a database. Not for groundedness — the
  deterministic check is strictly better whenever there's real data to
  check against.
"""

from __future__ import annotations
