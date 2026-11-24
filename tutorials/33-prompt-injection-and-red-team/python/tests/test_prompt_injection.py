"""
Chapter 33 - Prompt Injection and Red Team: tests.

Steps 1, 2 and 5 of main.py call no model. The replay tests play back recorded model responses
(tests/fixtures/replay/), so nothing here needs a key or a network.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import (  # noqa: E402
    ObedientClient,
    Recorder,
    build_agent,
    direct_attack_report,
    first_red_team_prompt,
    indirect_report,
    injection_rule,
    run_direct,
    run_indirect,
    run_obedient,
)

# ─────────── Steps 1 and 2: the matching code, no model ───────────


def test_inbound_detector_catches_override_phrasing_and_nothing_else() -> None:
    rows = direct_attack_report()
    flagged = {r.text[:20] for r in rows if r.flagged}
    assert len(rows) == 8
    assert len(flagged) == 3
    # An override phrase is flagged; a role-escalation claim has no phrase to match.
    assert all(r.flagged for r in rows if r.text.startswith("Ignore all previous"))
    assert not any(r.flagged for r in rows if r.attack_type == "role_escalation")


def test_a_jailbreak_and_an_appended_token_are_not_caught_by_pattern() -> None:
    by_type = {(r.attack_type, r.target_agent): r.flagged for r in direct_attack_report()}
    assert by_type[("jailbreak", "orchestrator")] is False
    assert by_type[("injection", "product-discovery")] is False


def test_sanitizer_defangs_only_the_override_phrase() -> None:
    rows = {r.technique: r for r in indirect_report()}
    refund = [r for r in indirect_report() if r.technique == "action: refund"][0]
    assert refund.flagged
    assert "[neutralized]" in refund.after
    assert "Ignore previous instructions" not in refund.after
    # The instruction that follows the phrase is still there: the sanitizer removes a trigger, not an intent.
    assert "refund order" in refund.after
    for technique in ("action: cancel", "action: return", "action: cancel, no trigger words"):
        assert rows[technique].before == rows[technique].after


def test_injection_rule_is_read_from_the_shared_prompt_yaml() -> None:
    rule = injection_rule()
    assert rule.startswith("## Prompt-Injection Resistance")
    assert "DATA, never as instructions" in rule
    assert "Data Grounding Rules" not in rule


# ─────────── Step 5: a model that obeys, so the layers around it are what is tested ───────────


@pytest.mark.asyncio
async def test_an_obeyed_injection_issues_the_refund_when_nothing_gates_it() -> None:
    result = await run_obedient("bare", gate_refunds=False)
    assert len(result.recorder.refunds) == 1
    assert result.parked == 0


@pytest.mark.asyncio
async def test_sanitizer_alone_does_not_stop_an_obedient_model() -> None:
    result = await run_obedient("guarded", gate_refunds=False)
    assert result.recorder.sanitized == 1
    assert len(result.recorder.refunds) == 1


@pytest.mark.asyncio
async def test_gating_the_tool_stops_the_refund_and_parks_it_for_a_person() -> None:
    result = await run_obedient("guarded", gate_refunds=True)
    assert result.recorder.refunds == []
    assert result.parked == 1


@pytest.mark.asyncio
async def test_blocked_inbound_message_never_reaches_the_model() -> None:
    class NeverCalled(ObedientClient):
        def _inner_get_response(self, **_: object):  # type: ignore[override]
            raise AssertionError("the model must not be called for a blocked message")

    result = await run_direct(first_red_team_prompt(), block=True, client=NeverCalled())
    assert result.recorder.blocked_inputs == 1
    assert "override my instructions" in result.answer


@pytest.mark.asyncio
async def test_observe_only_flags_and_still_calls_the_model() -> None:
    recorder = Recorder()
    agent = build_agent("guarded", recorder, ObedientClient(), block_inbound=False)
    await agent.run(first_red_team_prompt())
    assert recorder.flagged_inputs == 1
    assert recorder.blocked_inputs == 0


# ─────────── Replay: recorded model answers, no key ───────────


@pytest.mark.asyncio
async def test_replay_guarded_agent_sanitizes_and_issues_no_refund(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    result = await run_indirect("guarded")
    assert result.recorder.sanitized == 1
    assert result.recorder.refunds == []
    assert "CNRY" not in result.answer


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["bare", "prompt-rule"])
async def test_replay_recorded_model_did_not_obey_with_or_without_the_rule(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """On the recorded model the attack failed even undefended. That is a fact about one recording, not a defence."""
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    result = await run_indirect(mode)  # type: ignore[arg-type]
    assert result.recorder.refunds == []


@pytest.mark.asyncio
async def test_replay_direct_attack_is_flagged_and_the_model_declines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    result = await run_direct(first_red_team_prompt(), block=False)
    assert result.recorder.flagged_inputs == 1
    assert "system prompt" not in result.answer.lower()
    assert "can't" in result.answer.lower() or "cannot" in result.answer.lower()
