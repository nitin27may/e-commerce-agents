using System.Text.Json;
using Microsoft.Agents.AI.Workflows;

namespace ECommerceAgents.Shared.Workflows;

/// <summary>
/// Pre-purchase research workflow — .NET parity port of
/// <c>agents/python/workflows/pre_purchase.py</c>, now on MAF's real
/// <c>WorkflowBuilder</c> (issue #17, piece A) rather than a hand-rolled
/// <c>Task.WhenAll</c> fan-out.
/// </summary>
/// <remarks>
/// Six executors mirror Python's six classes exactly (same ids, same
/// responsibilities): <c>fan-out</c> broadcasts the initial state;
/// <c>reviews</c>/<c>stock</c>/<c>price-history</c> each call one tool and
/// forward; <c>merge-and-ship</c> is the fan-in barrier — it can't share one
/// mutable <see cref="ResearchState"/> across three concurrent branches the
/// way the old <c>Task.WhenAll</c> version did, so it collects the three
/// partial states <c>AddFanInBarrierEdge</c> delivers one at a time and
/// merges them (<see cref="MergeStates"/>, the .NET twin of Python's
/// <c>_merge_states</c>) before conditionally running the shipping
/// estimate; <c>synthesis</c> builds the recommendation string and yields
/// it as the workflow's output. A fresh executor set and workflow graph is
/// built on every call — same as Python's own
/// <c>_build_maf_workflow()</c>, called once per <c>execute()</c> — so an
/// executor's instance-level mutable state (the merge barrier's collected-
/// inputs list) is always scoped to exactly one run.
/// The public <c>ExecuteAsync(ResearchState, CancellationToken)</c>
/// signature is unchanged, so existing callers don't need to change.
/// </remarks>
public sealed class PrePurchaseWorkflow
{
    private readonly IPrePurchaseTools _tools;

    public PrePurchaseWorkflow(IPrePurchaseTools tools)
    {
        _tools = tools ?? throw new ArgumentNullException(nameof(tools));
    }

    public async Task<ResearchState> ExecuteAsync(ResearchState state, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(state);

        var fanOut = new FanOutExecutor();
        var reviews = new ReviewsExecutor(_tools, ct);
        var stock = new StockExecutor(_tools, ct);
        var price = new PriceHistoryExecutor(_tools, ct);
        var merge = new MergeAndShipExecutor(_tools, ct);
        var synthesis = new SynthesisExecutor();

        var workflow = new WorkflowBuilder(fanOut)
            .AddFanOutEdge(fanOut, new ExecutorBinding[] { reviews, stock, price })
            .AddFanInBarrierEdge(new ExecutorBinding[] { reviews, stock, price }, merge)
            .AddEdge(merge, synthesis)
            .WithOutputFrom(synthesis)
            .Build();

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, state, cancellationToken: ct);

        var finalState = state;
        await foreach (var evt in run.WatchStreamAsync(ct))
        {
            if (evt is WorkflowOutputEvent output && output.Data is ResearchState s)
            {
                finalState = s;
            }
        }
        return finalState;
    }

    // ─────────────────────── Executors ───────────────────────

    [SendsMessage(typeof(ResearchState))]
    private sealed class FanOutExecutor() : Executor<ResearchState>("fan-out")
    {
        public override async ValueTask HandleAsync(ResearchState state, IWorkflowContext context, CancellationToken ct = default) =>
            await context.SendMessageAsync(state, ct);
    }

    [SendsMessage(typeof(ResearchState))]
    private sealed class ReviewsExecutor(IPrePurchaseTools tools, CancellationToken outerCt) : Executor<ResearchState>("reviews")
    {
        public override async ValueTask HandleAsync(ResearchState state, IWorkflowContext context, CancellationToken ct = default)
        {
            await RunStepAsync("reviews", state, async s => s.Reviews = await tools.AnalyzeSentimentAsync(s.ProductId, outerCt), outerCt);
            await context.SendMessageAsync(state, ct);
        }
    }

    [SendsMessage(typeof(ResearchState))]
    private sealed class StockExecutor(IPrePurchaseTools tools, CancellationToken outerCt) : Executor<ResearchState>("stock")
    {
        public override async ValueTask HandleAsync(ResearchState state, IWorkflowContext context, CancellationToken ct = default)
        {
            await RunStepAsync("stock", state, async s => s.Stock = await tools.CheckStockAsync(s.ProductId, outerCt), outerCt);
            await context.SendMessageAsync(state, ct);
        }
    }

    [SendsMessage(typeof(ResearchState))]
    private sealed class PriceHistoryExecutor(IPrePurchaseTools tools, CancellationToken outerCt) : Executor<ResearchState>("price-history")
    {
        public override async ValueTask HandleAsync(ResearchState state, IWorkflowContext context, CancellationToken ct = default)
        {
            await RunStepAsync("price_history", state, async s => s.PriceHistory = await tools.GetPriceHistoryAsync(s.ProductId, 90, outerCt), outerCt);
            await context.SendMessageAsync(state, ct);
        }
    }

    /// <summary>
    /// Fan-in barrier target. <c>AddFanInBarrierEdge</c> holds the three
    /// upstream messages until all three sources have produced one, then
    /// delivers them one at a time within the same superstep — not as a
    /// single batched list — so this collects into <see cref="_received"/>
    /// and only merges/forwards once all three have arrived.
    /// </summary>
    [SendsMessage(typeof(ResearchState))]
    private sealed class MergeAndShipExecutor(IPrePurchaseTools tools, CancellationToken outerCt) : Executor<ResearchState>("merge-and-ship")
    {
        private readonly List<ResearchState> _received = [];

        public override async ValueTask HandleAsync(ResearchState state, IWorkflowContext context, CancellationToken ct = default)
        {
            _received.Add(state);
            if (_received.Count < 3)
            {
                return;
            }

            var merged = MergeStates(_received);
            if (IsInStock(merged.Stock))
            {
                await RunStepAsync("shipping", merged, async s => s.Shipping = await tools.EstimateShippingAsync(s.ProductId, s.UserRegion, outerCt), outerCt);
            }

            await context.SendMessageAsync(merged, ct);
        }
    }

    [YieldsOutput(typeof(ResearchState))]
    private sealed class SynthesisExecutor() : Executor<ResearchState>("synthesis")
    {
        public override async ValueTask HandleAsync(ResearchState state, IWorkflowContext context, CancellationToken ct = default)
        {
            state.Recommendation = BuildRecommendation(state);
            await context.YieldOutputAsync(state, ct);
        }
    }

    // ─────────────────────── Helpers ───────────────────────

    private static async Task RunStepAsync(
        string step,
        ResearchState state,
        Func<ResearchState, Task> body,
        CancellationToken ct
    )
    {
        try
        {
            await body(state);
            state.CompletedSteps.Add(step);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex)
        {
            state.Errors.Add($"{step}: {ex.Message}");
        }
    }

    /// <summary>Combines three partial <see cref="ResearchState"/>s into one — the .NET twin of Python's <c>_merge_states</c>.</summary>
    private static ResearchState MergeStates(List<ResearchState> inputs)
    {
        var merged = new ResearchState(inputs[0].ProductId, inputs[0].UserRegion);
        foreach (var partial in inputs)
        {
            if (partial.Reviews is not null)
            {
                merged.Reviews = partial.Reviews;
            }
            if (partial.Stock is not null)
            {
                merged.Stock = partial.Stock;
            }
            if (partial.PriceHistory is not null)
            {
                merged.PriceHistory = partial.PriceHistory;
            }
            foreach (var step in partial.CompletedSteps)
            {
                if (!merged.CompletedSteps.Contains(step))
                {
                    merged.CompletedSteps.Add(step);
                }
            }
            foreach (var error in partial.Errors)
            {
                if (!merged.Errors.Contains(error))
                {
                    merged.Errors.Add(error);
                }
            }
        }
        return merged;
    }

    private static bool IsInStock(JsonElement? stock)
    {
        if (stock is null) return false;
        if (stock.Value.ValueKind != JsonValueKind.Object) return false;
        return stock.Value.TryGetProperty("in_stock", out var v)
            && v.ValueKind == JsonValueKind.True;
    }

    private static string BuildRecommendation(ResearchState state)
    {
        var parts = new List<string>();

        if (state.Reviews is { ValueKind: JsonValueKind.Object } r
            && r.TryGetProperty("sentiment", out var sentiment)
            && sentiment.ValueKind == JsonValueKind.String)
        {
            int total = r.TryGetProperty("total_reviews", out var t) && t.TryGetInt32(out var ti) ? ti : 0;
            parts.Add($"Reviews: {sentiment.GetString()} ({total} reviews)");
        }

        if (state.Stock is { ValueKind: JsonValueKind.Object } s
            && s.TryGetProperty("in_stock", out var inStock)
            && inStock.ValueKind == JsonValueKind.True)
        {
            int qty = s.TryGetProperty("total_quantity", out var q) && q.TryGetInt32(out var qi) ? qi : 0;
            parts.Add($"Stock: {qty} units available");
        }
        else
        {
            parts.Add("Stock: Currently out of stock");
        }

        if (state.PriceHistory is { ValueKind: JsonValueKind.Object } p)
        {
            bool isGoodDeal = p.TryGetProperty("is_good_deal", out var g) && g.ValueKind == JsonValueKind.True;
            if (isGoodDeal)
            {
                decimal avg = p.TryGetProperty("average_price", out var a) && a.TryGetDecimal(out var av) ? av : 0m;
                parts.Add($"Price: Good deal (below {avg:F0} avg)");
            }
            else if (p.TryGetProperty("trend", out var trend) && trend.ValueKind == JsonValueKind.String)
            {
                parts.Add($"Price trend: {trend.GetString()}");
            }
        }

        if (state.Shipping is { ValueKind: JsonValueKind.Object } sh
            && sh.TryGetProperty("options", out var options)
            && options.ValueKind == JsonValueKind.Array
            && options.GetArrayLength() > 0)
        {
            var cheapest = options[0];
            decimal price = cheapest.TryGetProperty("price", out var pr) && pr.TryGetDecimal(out var prd) ? prd : 0m;
            string days = cheapest.TryGetProperty("days", out var d) && d.ValueKind != JsonValueKind.Null
                ? d.ToString()
                : "N/A";
            parts.Add($"Shipping: from ${price:F2}, {days} days");
        }

        return parts.Count == 0
            ? "Insufficient data for recommendation"
            : string.Join(" | ", parts);
    }
}
