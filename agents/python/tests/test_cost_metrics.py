"""The cost estimate leaves the process as a metric, not only as a log line.

``shared/telemetry.py`` has exposed ``get_meter()`` since telemetry was wired
up and nothing ever called it — every metric on the dashboard came from MAF's
or FastAPI's instrumentation. Cost is the one number this application knows
and they do not, and it was reachable only by grepping logs, which is not
something an OTLP sink can alert on.

These use a real in-memory ``MeterProvider`` rather than a mock: the failure
worth catching is an instrument that is *built* but bound to the wrong (no-op)
provider, and a mock asserts the call happened while proving nothing about
whether a reader would ever see it.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from shared import metrics as cost_metrics
from shared.config import settings


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    """Install a real SDK MeterProvider and hand back its reader."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    # Patched at this repo's own seam rather than OTel's global provider:
    # set_meter_provider is process-wide and one-shot, so a test that used it
    # would leak into every later test in the session.
    import shared.telemetry

    monkeypatch.setattr(shared.telemetry, "get_meter", provider.get_meter)
    monkeypatch.setattr(settings, "OTEL_ENABLED", True)
    cost_metrics._reset_for_tests()
    yield reader
    cost_metrics._reset_for_tests()


def _points(reader: InMemoryMetricReader, name: str) -> list:
    data = reader.get_metrics_data()
    out = []
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    out.extend(metric.data.data_points)
    return out


def test_a_priced_turn_reaches_a_reader(reader: InMemoryMetricReader) -> None:
    cost_metrics.record_llm_turn_cost(
        0.0042, model="gpt-4.1", tokens_in=1200, tokens_out=300, agent="orchestrator", mode="observe"
    )

    points = _points(reader, "ecommerce.llm.cost.usd")
    assert len(points) == 1
    assert points[0].value == pytest.approx(0.0042)
    assert points[0].attributes["model"] == "gpt-4.1"
    assert points[0].attributes["agent"] == "orchestrator"


def test_turns_accumulate_rather_than_overwrite(reader: InMemoryMetricReader) -> None:
    """A Counter is the right instrument: spend is monotonic within a process,
    and an alert wants the delta over a window, not the last turn's price."""
    for _ in range(3):
        cost_metrics.record_llm_turn_cost(0.01, model="gpt-4.1", tokens_in=10, tokens_out=5)

    points = _points(reader, "ecommerce.llm.cost.usd")
    assert len(points) == 1
    assert points[0].value == pytest.approx(0.03)


def test_tokens_are_split_by_direction(reader: InMemoryMetricReader) -> None:
    """Cost is derived from tokens through a hand-edited price table. When
    spend jumps, only the raw counts say whether traffic or pricing moved."""
    cost_metrics.record_llm_turn_cost(0.01, model="gpt-4.1", tokens_in=1000, tokens_out=250)

    by_direction = {p.attributes["direction"]: p.value for p in _points(reader, "ecommerce.llm.tokens")}
    assert by_direction == {"input": 1000, "output": 250}


def test_no_user_scoped_attribute_is_ever_attached(reader: InMemoryMetricReader) -> None:
    """One time series per customer is both a metrics-cost problem and a way to
    leak identity into a backend with no business holding it. The signature has
    no parameter for it — this pins that no future edit adds one quietly."""
    cost_metrics.record_llm_turn_cost(
        0.01, model="gpt-4.1", tokens_in=10, tokens_out=5, agent="orchestrator", mode="observe"
    )

    attributes = set(_points(reader, "ecommerce.llm.cost.usd")[0].attributes)
    assert attributes == {"model", "agent", "mode"}


def test_recording_is_a_no_op_when_telemetry_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing may be built when OTEL_ENABLED is false — instruments bound to
    the default no-op provider would be silently discarded anyway, and the
    lazy build exists precisely to avoid binding before configure_telemetry."""
    monkeypatch.setattr(settings, "OTEL_ENABLED", False)
    cost_metrics._reset_for_tests()

    cost_metrics.record_llm_turn_cost(0.01, model="gpt-4.1", tokens_in=10, tokens_out=5)

    assert cost_metrics._instruments is None


def test_a_broken_metrics_backend_cannot_fail_a_request(
    reader: InMemoryMetricReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A misconfigured sink is an operations problem, not a reason a customer's
    question errors."""
    instruments = cost_metrics._get_instruments()

    class _Exploding:
        def add(self, *_args, **_kwargs):
            raise RuntimeError("collector unreachable")

    monkeypatch.setitem(instruments, "cost", _Exploding())

    cost_metrics.record_llm_turn_cost(0.01, model="gpt-4.1", tokens_in=10, tokens_out=5)
