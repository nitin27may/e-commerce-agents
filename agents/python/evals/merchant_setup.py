"""Eval setup for the merchant-ops suite: make sure the demo sellers have a pending proposal batch (#298).

The merchant-ops agent explains proposals that already exist, and a freshly seeded database has none (batches are
generated on request or by the daily run). So before a merchant-ops run the harness generates one batch for each
demo seller with the same deterministic generator the product uses, :func:`shared.merchant.proposals.generate_proposal`
(no model). It is idempotent: a seller who already has a pending batch is skipped (the generator caps a batch, so
running it again would open a second one with the next markdowns), so a second run, or a repeat, creates nothing.

Given a fresh seed the batch is the same every time (the seeder is deterministic and the generator is a pure function
of the catalogue), which is what lets the replay fixtures for the suite stay reachable.
"""

from __future__ import annotations

import logging

from shared.db import get_pool
from shared.log_safe import log_safe
from shared.merchant.proposals import generate_proposal

logger = logging.getLogger(__name__)

DEMO_SELLERS = ("seller.demo@gmail.com", "seller2.demo@gmail.com")


async def ensure_pending_proposals() -> int:
    """Generate a batch for each demo seller that exists; returns how many batches were created."""
    pool = get_pool()
    created = 0
    for email in DEMO_SELLERS:
        seller_id = await pool.fetchval("SELECT id FROM users WHERE email = $1", email)
        if seller_id is None:
            logger.warning("eval.merchant_setup.no_seller email=%s", log_safe(email))
            continue
        if await pool.fetchval("SELECT 1 FROM proposals WHERE seller_id = $1 AND status = 'pending_review'", seller_id):
            continue  # the generator caps a batch, so a second run would open a second batch with the next markdowns
        result = await generate_proposal(pool, seller_id, trigger="manual")
        created += 1 if result.proposal_id else 0
    logger.info("eval.merchant_setup.done created=%d", created)
    return created
