"""The capability flags `/api/orchestration/modes` advertises match what each mode does (and the .NET twin)."""

from __future__ import annotations

from orchestrator.modes import MODES, list_modes


def test_checkpointing_workflow_modes_say_so() -> None:
    assert MODES["workflow:pre-purchase"].capabilities.supports_checkpoints is True
    assert MODES["workflow:return-replace"].capabilities.supports_checkpoints is True


def test_modes_without_checkpoints_say_so() -> None:
    for name in ("tool", "handoff", "group-chat"):
        assert MODES[name].capabilities.supports_checkpoints is False, name


def test_listing_carries_the_flag() -> None:
    by_name = {m["name"]: m for m in list_modes()}
    assert by_name["workflow:return-replace"]["capabilities"]["supports_checkpoints"] is True
