"""evals.seed_adversarial writes real rows to DATABASE_URL, so it refuses to run without --yes."""

from __future__ import annotations

import pytest

from evals import seed_adversarial


@pytest.fixture
def connects(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    calls: list[bool] = []

    async def fake_main(do_remove: bool) -> None:
        calls.append(do_remove)

    monkeypatch.setattr(seed_adversarial, "_main", fake_main)
    return calls


@pytest.mark.parametrize("argv", [[], ["--remove"]])
def test_without_yes_nothing_runs_and_the_target_is_named(
    argv: list[str], connects: list[bool], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:hunter2@db.example:5433/shop")

    with pytest.raises(SystemExit) as exc:
        seed_adversarial.main(argv)

    assert exc.value.code == 2
    assert connects == []
    err = capsys.readouterr().err
    assert "db.example:5433/shop" in err and "--yes" in err
    assert "hunter2" not in err, "credentials are never echoed"


def test_with_yes_it_runs(connects: list[bool]) -> None:
    seed_adversarial.main(["--yes"])
    seed_adversarial.main(["--yes", "--remove"])

    assert connects == [False, True]
