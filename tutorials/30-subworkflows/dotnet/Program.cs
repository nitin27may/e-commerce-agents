// MAF v1 — Chapter 30: Subworkflows (.NET)
//
// Two small workflows. The inner one, "find replacement", validates a proposed
// replacement product against a toy catalogue, checks a toy stock count, and
// approves or rejects it. The outer one, "process return", uses the inner
// workflow as a single node of its own graph.
//
// The .NET seam is SubworkflowBinding — a Workflow wrapped so it satisfies
// ExecutorBinding and can go anywhere an executor can. Python calls the same
// thing WorkflowExecutor. Both mean: a workflow is composable, and the outer
// graph does not need to know how many steps are hiding inside the node.
//
// No LLM — both workflows are pure, deterministic graph logic (same LLM-free
// precedent as chapter 09), so the mechanics of nesting stay front and centre.
//
// Two things about nesting that are easy to get wrong:
//
//   1. Build a FRESH inner workflow per wrapper. Sharing one Workflow instance
//      shares its executor instances, and executor state is not designed to be
//      concurrent. build_find_replacement is a factory for exactly this reason.
//   2. The inner workflow's output becomes a MESSAGE to the outer node's
//      downstream edge, not the outer workflow's terminal output. That is what
//      lets finalize_return reshape it. If the outer workflow yielded the inner
//      one's result directly, the nesting would buy nothing over inlining.
//
// Run:
//   cd tutorials/30-subworkflows/dotnet
//   dotnet run

using Microsoft.Agents.AI.Workflows;

namespace MafV1.Ch30.Subworkflows;

// ─────────────── Messages ───────────────

/// <summary>Input to the inner "find replacement" workflow.</summary>
public sealed record ReplacementRequest(string OrderId, string RequestedProductId);

/// <summary>Output of the inner "find replacement" workflow.</summary>
public sealed record ReplacementResult(string OrderId, string ProductId, bool Approved, string Reason);

/// <summary>Input to the outer "process return" workflow.</summary>
public sealed record ReturnRequest(string OrderId, string RequestedProductId);

// ─────────────── Inner workflow executors ───────────────

/// <summary>Step 1: does the requested product exist in the catalogue at all?</summary>
[SendsMessage(typeof(ReplacementRequest))]
[YieldsOutput(typeof(ReplacementResult))]
internal sealed partial class ValidateCatalogExecutor() : Executor("validate_catalog")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        ReplacementRequest request,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        if (!Program.Catalog.ContainsKey(request.RequestedProductId))
        {
            // Short-circuit exit. Yielding here rather than forwarding is what
            // stops the stock check running on a product that does not exist.
            await context.YieldOutputAsync(
                new ReplacementResult(request.OrderId, request.RequestedProductId, false, "not found in catalog"),
                cancellationToken);
            return;
        }

        await context.SendMessageAsync(request, cancellationToken: cancellationToken);
    }
}

/// <summary>Step 2: is there stock left for the now-known-to-exist product?</summary>
[SendsMessage(typeof(ReplacementRequest))]
[YieldsOutput(typeof(ReplacementResult))]
internal sealed partial class CheckStockExecutor() : Executor("check_stock")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        ReplacementRequest request,
        IWorkflowContext context,
        CancellationToken cancellationToken = default)
    {
        if (Program.Stock.GetValueOrDefault(request.RequestedProductId) <= 0)
        {
            await context.YieldOutputAsync(
                new ReplacementResult(request.OrderId, request.RequestedProductId, false, "out of stock"),
                cancellationToken);
            return;
        }

        await context.SendMessageAsync(request, cancellationToken: cancellationToken);
    }
}

/// <summary>Step 3: both checks passed — approve the replacement.</summary>
[YieldsOutput(typeof(ReplacementResult))]
internal sealed partial class ApproveExecutor() : Executor("approve")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        ReplacementRequest request,
        IWorkflowContext context,
        CancellationToken cancellationToken = default) =>
        await context.YieldOutputAsync(
            new ReplacementResult(
                request.OrderId,
                request.RequestedProductId,
                true,
                $"in stock: {Program.Catalog[request.RequestedProductId]}"),
            cancellationToken);
}

// ─────────────── Outer workflow executors ───────────────

/// <summary>Translates the outer request into the inner workflow's input type.</summary>
[SendsMessage(typeof(ReplacementRequest))]
internal sealed partial class ReceiveReturnExecutor() : Executor("receive_return")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        ReturnRequest request,
        IWorkflowContext context,
        CancellationToken cancellationToken = default) =>
        await context.SendMessageAsync(
            new ReplacementRequest(request.OrderId, request.RequestedProductId),
            cancellationToken: cancellationToken);
}

/// <summary>Turns the subworkflow's result into the outer workflow's final text.</summary>
[YieldsOutput(typeof(string))]
internal sealed partial class FinalizeReturnExecutor() : Executor("finalize_return")
{
    [MessageHandler]
    public async ValueTask HandleAsync(
        ReplacementResult result,
        IWorkflowContext context,
        CancellationToken cancellationToken = default) =>
        await context.YieldOutputAsync(
            result.Approved
                ? $"Return {result.OrderId}: replacement {result.ProductId} approved and shipped ({result.Reason})."
                : $"Return {result.OrderId}: replacement {result.ProductId} rejected ({result.Reason}) "
                  + "— issuing a refund instead.",
            cancellationToken);
}

public static class Program
{
    // ─────────────── Toy catalogue data ───────────────

    public static readonly IReadOnlyDictionary<string, string> Catalog = new Dictionary<string, string>
    {
        ["sku-mug-red"] = "Ceramic Mug — Red",
        ["sku-mug-blue"] = "Ceramic Mug — Blue",
        ["sku-plate-green"] = "Dinner Plate — Green",
    };

    public static readonly IReadOnlyDictionary<string, int> Stock = new Dictionary<string, int>
    {
        ["sku-mug-red"] = 12,
        ["sku-mug-blue"] = 0, // in the catalogue, but out of stock
        ["sku-plate-green"] = 5,
    };

    // ─────────────── Builders ───────────────

    /// <summary>Builds a FRESH instance of the inner workflow.</summary>
    /// <remarks>
    /// A factory, not a singleton, and deliberately so. Sharing one Workflow
    /// across wrappers shares its executor instances, whose state is not
    /// designed for concurrent use — and the resulting corruption is silent.
    /// </remarks>
    public static Workflow BuildFindReplacementWorkflow()
    {
        var validate = new ValidateCatalogExecutor();
        var stock = new CheckStockExecutor();
        var approve = new ApproveExecutor();

        return new WorkflowBuilder(validate)
            .AddEdge(validate, stock)
            .AddEdge(stock, approve)
            .WithOutputFrom(validate, stock, approve)
            .Build();
    }

    /// <summary>Builds the outer workflow, nesting a fresh inner one inside it.</summary>
    public static Workflow BuildProcessReturnWorkflow()
    {
        var receive = new ReceiveReturnExecutor();
        var finalize = new FinalizeReturnExecutor();

        // The nesting. SubworkflowBinding makes a whole Workflow satisfy
        // ExecutorBinding, so it can sit on an edge like any single executor.
        // The outer graph has three nodes; one of them happens to be three more.
        var findReplacement = new SubworkflowBinding(
            BuildFindReplacementWorkflow(),
            "find_replacement",
            ExecutorOptions.Default);

        return new WorkflowBuilder(receive)
            .AddEdge(receive, findReplacement)
            .AddEdge(findReplacement, finalize)
            .WithOutputFrom(finalize)
            .Build();
    }

    // ─────────────── Run helpers ───────────────

    /// <summary>Runs the inner workflow standalone and returns its single result.</summary>
    public static async Task<ReplacementResult?> RunFindReplacementAsync(string orderId, string productId)
    {
        Workflow workflow = BuildFindReplacementWorkflow();
        var request = new ReplacementRequest(orderId, productId);

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, request);
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            if (evt is WorkflowOutputEvent { Data: ReplacementResult result })
            {
                return result;
            }
        }

        return null;
    }

    /// <summary>Runs the outer workflow and returns every output it yielded.</summary>
    public static async Task<IReadOnlyList<string>> RunProcessReturnAsync(string orderId, string productId)
    {
        Workflow workflow = BuildProcessReturnWorkflow();
        var request = new ReturnRequest(orderId, productId);
        var outputs = new List<string>();

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, request);
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            if (evt is WorkflowOutputEvent { Data: string text })
            {
                outputs.Add(text);
            }
        }

        return outputs;
    }

    public static async Task<int> Main()
    {
        (string OrderId, string ProductId)[] scenarios =
        {
            ("R-1001", "sku-mug-red"),   // in catalogue, in stock  -> approved
            ("R-1002", "sku-mug-blue"),  // in catalogue, no stock  -> rejected
            ("R-1003", "sku-unknown"),   // not in catalogue at all -> rejected
        };

        foreach ((string orderId, string productId) in scenarios)
        {
            Console.WriteLine($"--- Return {orderId}: requested replacement '{productId}' ---");
            foreach (string output in await RunProcessReturnAsync(orderId, productId))
            {
                Console.WriteLine(output);
            }

            Console.WriteLine();
        }

        return 0;
    }
}
