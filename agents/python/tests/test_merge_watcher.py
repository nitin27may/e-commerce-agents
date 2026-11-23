"""scripts/merge-when-green.sh, driven by a fake `gh` that replays scripted JSON."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "merge-when-green.sh"
OLD, NEW = "a" * 40, "b" * 40

FAKE_GH = """#!/usr/bin/env bash
# `pr view` answers with the Nth scripted payload (the last repeats); `pr merge` is logged.
d="$FAKE_GH_DIR"
case "$1 $2" in
  "pr view")
    n=$(( $(cat "$d/count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$d/count"
    f="$d/view.$n"; [ -f "$f" ] || f="$(ls "$d"/view.* | sort -t. -k2 -n | tail -1)"
    if [[ " $* " == *" --jq "* ]]; then
      python3 -c 'import json,sys; print(json.load(sys.stdin)["headRefOid"])' < "$f"
    else
      cat "$f"
    fi ;;
  "pr merge") echo "$*" >> "$d/merges" ;;
  *) echo "unexpected: $*" >&2; exit 9 ;;
esac
"""


def snapshot(sha: str = OLD, n: int = 20, mergeable: str = "MERGEABLE", **over: str) -> dict:
    checks = [{"name": f"c{i}", "status": "COMPLETED", "conclusion": "SUCCESS"} for i in range(n)]
    if n:
        checks[0]["conclusion"] = "SKIPPED"
    if over:
        checks[1].update(over)
    return {"headRefOid": sha, "mergeable": mergeable, "statusCheckRollup": checks}


def run(tmp_path: Path, views: list[dict], **env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    gh = tmp_path / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    for i, v in enumerate(views, 1):
        (tmp_path / f"view.{i}").write_text(json.dumps(v))
    full = {**os.environ, "FAKE_GH_DIR": str(tmp_path), "MWG_GH": str(gh), "MWG_POLL_INTERVAL": "0", **env}
    proc = subprocess.run(["bash", str(SCRIPT), "7"], capture_output=True, text=True, env=full, timeout=60)
    merges = (tmp_path / "merges").read_text().splitlines() if (tmp_path / "merges").exists() else []
    return proc, merges


def test_all_green_and_stable_merges_once(tmp_path: Path) -> None:
    proc, merges = run(tmp_path, [snapshot()])
    assert proc.returncode == 0, proc.stderr
    assert merges == [f"pr merge 7 --squash --match-head-commit {OLD}"]
    assert "--admin" not in merges[0]


def test_head_sha_change_mid_watch_refuses(tmp_path: Path) -> None:
    # call 1 = start, call 2 = first poll, call 3 = second poll sees a late push
    proc, merges = run(tmp_path, [snapshot(OLD), snapshot(OLD), snapshot(NEW)])
    assert proc.returncode != 0
    assert "head SHA changed" in proc.stderr
    assert merges == []


def test_head_moved_between_start_and_first_poll_refuses(tmp_path: Path) -> None:
    proc, merges = run(tmp_path, [snapshot(OLD), snapshot(NEW)])
    assert proc.returncode != 0
    assert merges == []


def test_failing_check_refuses(tmp_path: Path) -> None:
    proc, merges = run(tmp_path, [snapshot(conclusion="FAILURE")])
    assert proc.returncode != 0
    assert "concluded FAILURE" in proc.stderr
    assert merges == []


def test_incomplete_check_refuses(tmp_path: Path) -> None:
    proc, merges = run(tmp_path, [snapshot(status="IN_PROGRESS", conclusion="")])
    assert proc.returncode != 0
    assert "not complete" in proc.stderr
    assert merges == []


def test_too_few_checks_refuses(tmp_path: Path) -> None:
    proc, merges = run(tmp_path, [snapshot(n=5)])
    assert proc.returncode != 0
    assert "need at least 20" in proc.stderr
    assert merges == []


def test_min_checks_is_configurable(tmp_path: Path) -> None:
    proc, merges = run(tmp_path, [snapshot(n=5)], MWG_MIN_CHECKS="3")
    assert proc.returncode == 0
    assert len(merges) == 1


@pytest.mark.parametrize("state", ["CONFLICTING", "UNKNOWN"])
def test_conflicts_refuse(tmp_path: Path, state: str) -> None:
    proc, merges = run(tmp_path, [snapshot(mergeable=state)])
    assert proc.returncode != 0
    assert "not mergeable" in proc.stderr
    assert merges == []


def test_bad_argument_exits_2() -> None:
    proc = subprocess.run(["bash", str(SCRIPT), "abc"], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
