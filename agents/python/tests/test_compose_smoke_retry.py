"""The compose smoke script's cold-start reads retry a bounded number of times, logged."""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "compose-smoke.sh"
ENV_BASE = {"PATH": "/usr/bin:/bin"}


def _retry3_source() -> str:
    text = SCRIPT.read_text()
    start = text.index("retry3() {")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def test_script_parses() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_login_and_products_go_through_retry3() -> None:
    text = SCRIPT.read_text()
    assert "retry3 login curl" in text
    assert "retry3 products curl" in text


def test_retry3_recovers_from_one_failure_and_gives_up_after_three(tmp_path: Path) -> None:
    counter = tmp_path / "n"
    counter.write_text("0")
    flaky = f'n=$(( $(cat {counter}) + 1 )); echo $n > {counter}; if [ "$n" -ge "$1" ]; then echo ok; else exit 1; fi'
    harness = f"sleep() {{ :; }}\n{_retry3_source()}\nretry3 flaky bash -c '{flaky}' _ \"$WANT\""

    ok = subprocess.run(["bash", "-c", harness], env={**ENV_BASE, "WANT": "2"}, capture_output=True, text=True)
    assert ok.returncode == 0
    assert ok.stdout == "ok"
    assert "attempt 1/3" in ok.stderr

    counter.write_text("0")
    bad = subprocess.run(["bash", "-c", harness], env={**ENV_BASE, "WANT": "9"}, capture_output=True, text=True)
    assert bad.returncode != 0
    assert counter.read_text().strip() == "3"
