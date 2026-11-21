"""B-15: history compaction keeps whole turns, and is off (byte-identical) by default."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from shared import config as _config
from shared.agent_host import _rehydrate_history_from_session, _run_agent_native
from shared.context import current_user_email
from shared.history_compaction import compact_history, group_history


def _turns(n: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(n):
        out.append({"role": "user", "content": f"q{i}"})
        out.append({"role": "assistant", "content": f"a{i}"})
    return out


def test_off_returns_the_same_list_object() -> None:
    history = _turns(30)
    assert compact_history(history, 0) is history
    assert compact_history(None, 5) is None


def test_under_the_window_is_untouched() -> None:
    history = _turns(3)
    assert compact_history(history, 10) is history


def test_keeps_last_n_turns_whole() -> None:
    kept = compact_history(_turns(30), 10)
    assert kept is not None
    assert len(group_history(kept)) == 10
    assert kept[0] == {"role": "user", "content": "q20"}
    assert kept[-1] == {"role": "assistant", "content": "a29"}


def test_tool_call_and_result_are_dropped_together() -> None:
    history = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "calling", "tool_calls": [{"id": "t1"}]},
        {"role": "tool", "content": "result", "tool_call_id": "t1"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "ok"},
    ]
    kept = compact_history(history, 1)
    assert kept == history[4:]
    assert all(m["role"] != "tool" for m in kept or [])
    # keeping two turns keeps the call and its result as a pair
    both = compact_history(history, 2) or []
    assert [m["role"] for m in both].count("tool") == 1


def test_leading_orphan_group_is_dropped_first() -> None:
    history = [{"role": "assistant", "content": "orphan"}, *_turns(2)]
    kept = compact_history(history, 2)
    assert kept == _turns(2)


class _CapturingAgent:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    async def run(self, messages: list[Any], **_: Any) -> Any:
        self.messages = messages

        class _Resp:
            text = "ok"
            additional_properties: dict[str, Any] = {}
            usage_details = None

        return _Resp()


async def _seed(clean_db, total: int) -> tuple[str, str]:
    user_id = uuid.uuid4()
    email = f"compact-{user_id}@example.com"
    await clean_db.execute(
        "INSERT INTO users (id, email, password_hash, name, role) VALUES ($1, $2, 'hash', 'T', 'customer')",
        user_id,
        email,
    )
    conv = await clean_db.fetchrow("INSERT INTO conversations (user_id, title) VALUES ($1, 't') RETURNING id", user_id)
    for i in range(total):
        await clean_db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES ($1, $2, $3)",
            conv["id"],
            "user" if i % 2 == 0 else "assistant",
            f"message {i}",
        )
    return str(conv["id"]), email


@pytest.mark.parametrize(("keep", "expected_history"), [(10, 20), (0, 50)])
async def test_rehydrated_history_reaches_the_model_trimmed(
    clean_db, monkeypatch: pytest.MonkeyPatch, keep: int, expected_history: int
) -> None:
    monkeypatch.setattr("shared.db.get_pool", lambda: clean_db)
    # Resolved at call time: test_env_aliases reloads shared.config, orphaning an import-time `settings`.
    monkeypatch.setattr(_config.settings, "HISTORY_COMPACTION_KEEP_LAST_GROUPS", keep)
    conversation_id, email = await _seed(clean_db, 60)
    current_user_email.set(email)

    history = await _rehydrate_history_from_session(conversation_id)
    agent = _CapturingAgent()
    await _run_agent_native(agent, "latest", history)

    # 10 turns (20 messages) when on; the fixed 50-row cap, unchanged, when off. Plus the current message.
    assert len(agent.messages) == expected_history + 1
    roles = [str(m.role) for m in agent.messages]
    assert roles[-1] == "user" and agent.messages[-1].text == "latest"
    if keep:
        assert roles[0] == "user", "a kept window starts on a user turn"
        assert agent.messages[0].text == "message 40"
