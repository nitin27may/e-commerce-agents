using System.Diagnostics;
using System.Diagnostics.Metrics;

namespace ECommerceAgents.Shared.Telemetry;

/// <summary>
/// The one OTel instrument this repo owns, as opposed to the ones the runtime emits for us.
/// </summary>
/// <remarks>
/// <para>
/// Every metric reaching the Aspire dashboard today comes from ASP.NET Core, HttpClient or
/// Npgsql instrumentation. Those measure latency and request counts honestly, but they cannot
/// know the one number this application does — what a run costs — which existed only as the
/// <c>cost_budget.exceeded</c> log line, and a log line is not something an OTLP sink can alert
/// on without shipping and parsing logs.
/// </para>
/// <para>
/// The twin of Python's <c>shared/metrics.py</c>, deliberately down to the instrument names:
/// a dashboard that has to ask which backend produced a series is a dashboard nobody builds.
/// </para>
/// <para>
/// <b>Attributes are deliberately low-cardinality.</b> Nothing user-scoped is attached —
/// <c>RequestContext.CurrentUserEmail</c> would turn one time series into one per customer,
/// which is both a metrics-cost problem and a way to leak identity into a telemetry backend
/// that has no business holding it.
/// </para>
/// <para>
/// <b>Cost here is an estimate, not a bill.</b> It is <see cref="Cost.CostEstimator"/>'s price
/// table applied to token counts, so it drifts whenever real pricing changes and is absent
/// entirely for any response that carries no usage. Alert on <em>change</em>, and reconcile
/// against the provider's own billing before believing a figure.
/// </para>
/// </remarks>
public static class CostMetrics
{
    /// <summary>
    /// Registered with the meter provider by <c>TelemetrySetup</c>. A meter nobody adds produces
    /// instruments that record into nothing, which is the failure mode worth naming here: the
    /// code looks correct and the dashboard stays empty.
    /// </summary>
    public const string MeterName = "ecommerce.cost";

    private static readonly Meter Meter = new(MeterName);

    private static readonly Counter<double> CostUsd = Meter.CreateCounter<double>(
        "ecommerce.llm.cost.usd",
        unit: "USD",
        description: "Estimated LLM spend, summed per turn from token usage.");

    private static readonly Counter<long> Tokens = Meter.CreateCounter<long>(
        "ecommerce.llm.tokens",
        unit: "{token}",
        description: "LLM tokens consumed, split by direction.");

    /// <summary>Records one priced LLM turn.</summary>
    /// <remarks>
    /// Tokens are recorded beside the dollar figure because cost is <em>derived</em> from them
    /// through a price table that is edited by hand. When spend jumps, the first question is
    /// whether the traffic changed or the table did, and only the raw counts can answer it.
    /// </remarks>
    public static void RecordTurn(double costUsd, string model, long tokensIn, long tokensOut, string agent = "", string mode = "")
    {
        var tags = new TagList { { "model", string.IsNullOrEmpty(model) ? "unknown" : model } };
        if (!string.IsNullOrEmpty(agent))
        {
            tags.Add("agent", agent);
        }
        if (!string.IsNullOrEmpty(mode))
        {
            tags.Add("mode", mode);
        }

        CostUsd.Add(costUsd, tags);

        var inputTags = tags;
        inputTags.Add("direction", "input");
        Tokens.Add(tokensIn, inputTags);

        var outputTags = tags;
        outputTags.Add("direction", "output");
        Tokens.Add(tokensOut, outputTags);
    }
}
