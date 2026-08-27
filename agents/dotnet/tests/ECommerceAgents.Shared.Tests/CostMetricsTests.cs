using System.Diagnostics.Metrics;
using ECommerceAgents.Shared.Telemetry;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// The cost estimate leaves the process as a metric, not only as a log line —
/// the .NET twin of Python's <c>tests/test_cost_metrics.py</c>.
/// </summary>
/// <remarks>
/// Uses a real <see cref="MeterListener"/> rather than a mock. The failure worth catching is an
/// instrument that is built but never observed, and a mock asserts the call happened while
/// proving nothing about whether a listener would ever see it.
/// </remarks>
public sealed class CostMetricsTests
{
    private sealed record Measurement(string Name, double Value, Dictionary<string, object?> Tags);

    private static List<Measurement> Capture(Action act)
    {
        var captured = new List<Measurement>();
        using var listener = new MeterListener
        {
            InstrumentPublished = (instrument, l) =>
            {
                if (instrument.Meter.Name == CostMetrics.MeterName)
                {
                    l.EnableMeasurementEvents(instrument);
                }
            },
        };

        listener.SetMeasurementEventCallback<double>((inst, value, tags, _) =>
            captured.Add(new Measurement(inst.Name, value, tags.ToArray().ToDictionary(t => t.Key, t => t.Value))));
        listener.SetMeasurementEventCallback<long>((inst, value, tags, _) =>
            captured.Add(new Measurement(inst.Name, value, tags.ToArray().ToDictionary(t => t.Key, t => t.Value))));

        listener.Start();
        act();
        return captured;
    }

    [Fact]
    public void RecordTurn_ReachesAListener_WithTheModelAndAgentTagged()
    {
        var captured = Capture(() =>
            CostMetrics.RecordTurn(0.0042, "gpt-4.1", 1200, 300, agent: "orchestrator", mode: "observe"));

        var cost = captured.Single(m => m.Name == "ecommerce.llm.cost.usd");
        cost.Value.Should().BeApproximately(0.0042, 1e-9);
        cost.Tags["model"].Should().Be("gpt-4.1");
        cost.Tags["agent"].Should().Be("orchestrator");
        cost.Tags["mode"].Should().Be("observe");
    }

    [Fact]
    public void RecordTurn_SplitsTokensByDirection()
    {
        // Cost is derived from tokens through a hand-edited price table. When spend
        // jumps, only the raw counts say whether traffic moved or the table did.
        var captured = Capture(() => CostMetrics.RecordTurn(0.01, "gpt-4.1", 1000, 250));

        var tokens = captured.Where(m => m.Name == "ecommerce.llm.tokens").ToList();
        tokens.Should().HaveCount(2);
        tokens.Single(t => (string?)t.Tags["direction"] == "input").Value.Should().Be(1000);
        tokens.Single(t => (string?)t.Tags["direction"] == "output").Value.Should().Be(250);
    }

    [Fact]
    public void RecordTurn_AttachesNothingUserScoped()
    {
        // One time series per customer is both a metrics-cost problem and a way to
        // leak identity into a backend with no business holding it. The signature has
        // no parameter for it; this pins that no later edit adds one quietly.
        var captured = Capture(() =>
            CostMetrics.RecordTurn(0.01, "gpt-4.1", 10, 5, agent: "orchestrator", mode: "observe"));

        captured.Single(m => m.Name == "ecommerce.llm.cost.usd")
            .Tags.Keys.Should().BeEquivalentTo("model", "agent", "mode");
    }

    [Fact]
    public void RecordTurn_AccumulatesAcrossTurns()
    {
        // A Counter is the right instrument: spend is monotonic within a process, and
        // an alert wants the delta over a window, not the last turn's price.
        var captured = Capture(() =>
        {
            CostMetrics.RecordTurn(0.01, "gpt-4.1", 10, 5);
            CostMetrics.RecordTurn(0.02, "gpt-4.1", 10, 5);
        });

        captured.Where(m => m.Name == "ecommerce.llm.cost.usd").Sum(m => m.Value)
            .Should().BeApproximately(0.03, 1e-9);
    }

    [Fact]
    public void MeterName_MatchesPython_SoOneDashboardCoversBothStacks()
    {
        // A dashboard that has to ask which backend produced a series is a dashboard
        // nobody builds. Pinned against shared/metrics.py's meter name.
        CostMetrics.MeterName.Should().Be("ecommerce.cost");
    }
}
