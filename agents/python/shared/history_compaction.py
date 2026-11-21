"""Group-preserving trim of rehydrated conversation history (B-15).

MAF 1.26.0 does export ``SlidingWindowStrategy`` / ``apply_compaction`` from ``agent_framework``, but its
unit is one *message*: a window of N keeps N messages, which can start on an assistant reply and cuts a
turn in half. The history this app forwards is plain ``{role, content}`` dicts, so a small trimmer that
counts *turns* is simpler and gives the contract the config states: a group is a user message together with
every message up to the next user message (assistant text, and any tool call / tool result pair), and a group
is kept or dropped whole, so a tool result is never left without its call.

Off (``keep_last_groups <= 0``) returns the input list untouched, so the model request is byte-identical.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)


def group_history(history: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split ``history`` into groups, each starting at a ``user`` message.

    Messages before the first user message (a window cut that began mid-turn) form their own leading group,
    which is therefore the first to be dropped.
    """
    groups: list[list[dict[str, Any]]] = []
    for message in history:
        if message.get("role") == "user" or not groups:
            groups.append([])
        groups[-1].append(message)
    return groups


def compact_history(history: list[dict[str, Any]] | None, keep_last_groups: int) -> list[dict[str, Any]] | None:
    """Keep only the last ``keep_last_groups`` groups of ``history``; ``<= 0`` disables it."""
    if not history or keep_last_groups <= 0:
        return history
    groups = group_history(history)
    if len(groups) <= keep_last_groups:
        return history
    kept = [message for group in groups[-keep_last_groups:] for message in group]
    logger.info(
        "history.compacted groups_before=%d groups_kept=%d messages_before=%d messages_after=%d",
        len(groups),
        keep_last_groups,
        len(history),
        len(kept),
    )
    return kept


def compact_history_from_settings(history: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """``compact_history`` with the configured window (late-bound, so tests can rebind settings)."""
    from shared import config as _config

    return compact_history(history, _config.settings.HISTORY_COMPACTION_KEEP_LAST_GROUPS)
