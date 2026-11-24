"""The seeded price history gives get_price_history's 60-day window 59 points, and the demo recordings agree.

The seeder writes one row per day at ``now - 90 .. now - 1`` days. ``get_price_history`` keeps
``recorded_at >= NOW() - 60 days``, so the row seeded at exactly ``now - 60 days`` is already outside the window
by the time any query runs: 59 points, never 60. A recording made when the window held 60 would miss on every
fresh seed, because the tool result (``data_points`` and the average) is part of the replay key.
"""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts import seed as seed_mod  # noqa: E402

PRODUCT_ID = "00000000-0000-0000-0000-000000000001"
REPLAY_DIR = Path(__file__).resolve().parents[1] / "demo" / "fixtures" / "replay"


class _RecordingConnection:
    def __init__(self) -> None:
        self.recorded_at: list[datetime] = []

    async def execute(self, _sql: str, _product_id: Any, _price: Decimal, recorded_at: datetime) -> None:
        self.recorded_at.append(recorded_at)


async def _seeded_rows() -> tuple[list[datetime], datetime]:
    conn = _RecordingConnection()
    await seed_mod.seed_price_history(conn, [{"id": PRODUCT_ID, "price": 100}])  # type: ignore[arg-type]
    # Any query runs after the seed: a later clock only ever shrinks the window.
    return conn.recorded_at, datetime.now(UTC)


def _within(rows: list[datetime], queried_at: datetime, days: int) -> int:
    return sum(1 for recorded_at in rows if recorded_at >= queried_at - timedelta(days=days))


async def test_the_seed_writes_ninety_daily_rows_and_the_window_boundary_row_falls_outside() -> None:
    rows, queried_at = await _seeded_rows()

    assert len(rows) == 90
    assert _within(rows, queried_at, 90) == 89
    assert _within(rows, queried_at, 60) == 59
    assert _within(rows, queried_at, 30) == 29


def test_the_recorded_price_check_saw_the_same_window() -> None:
    points = {
        int(match)
        for path in REPLAY_DIR.glob("*.json")
        for match in re.findall(r'period_days\\": 60.*?data_points\\": (\d+)', path.read_text())
    }
    assert points == {59}, f"the 60-day recordings hold data_points {sorted(points)}; a fresh seed gives 59"
