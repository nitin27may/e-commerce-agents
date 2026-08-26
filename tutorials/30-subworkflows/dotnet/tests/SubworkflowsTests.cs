// MAF v1 — Chapter 30 tests (Subworkflows)
//
// Nesting is worth doing only if the inner workflow is genuinely reusable, so
// the tests exercise it BOTH ways: standalone, and as a node inside the outer
// graph. If it only worked in one shape it would not be a subworkflow, it
// would be a set of executors that happen to be grouped.
//
// No LLM — both graphs are deterministic, which means every assertion here is
// exact rather than "contains".

using FluentAssertions;
using Microsoft.Agents.AI.Workflows;
using Xunit;

namespace MafV1.Ch30.Subworkflows.Tests;

public sealed class SubworkflowsTests
{
    // ─────────────── The inner workflow, standalone ───────────────

    [Fact]
    public async Task An_In_Stock_Catalogue_Product_Is_Approved()
    {
        ReplacementResult? result = await Program.RunFindReplacementAsync("R-1", "sku-mug-red");

        result.Should().NotBeNull();
        result!.Approved.Should().BeTrue();
        result.Reason.Should().Contain("Ceramic Mug — Red");
        result.OrderId.Should().Be("R-1");
    }

    [Fact]
    public async Task A_Catalogue_Product_With_No_Stock_Is_Rejected_As_Out_Of_Stock()
    {
        ReplacementResult? result = await Program.RunFindReplacementAsync("R-2", "sku-mug-blue");

        result!.Approved.Should().BeFalse();
        result.Reason.Should().Be("out of stock");
    }

    [Fact]
    public async Task A_Product_Not_In_The_Catalogue_Is_Rejected_As_Not_Found()
    {
        // A different reason from "out of stock", and the difference matters:
        // one is a restock problem, the other is a bad SKU.
        ReplacementResult? result = await Program.RunFindReplacementAsync("R-3", "sku-unknown");

        result!.Approved.Should().BeFalse();
        result.Reason.Should().Be("not found in catalog");
    }

    [Fact]
    public async Task The_Catalogue_Check_Short_Circuits_Before_The_Stock_Check()
    {
        // Ordering, asserted through the reason. An unknown SKU has no stock
        // entry either, so a graph that ran the checks in the other order would
        // report "out of stock" for a product that does not exist — technically
        // true and completely misleading.
        ReplacementResult? result = await Program.RunFindReplacementAsync("R-3", "sku-unknown");

        result!.Reason.Should().NotBe("out of stock");
    }

    [Fact]
    public async Task The_Inner_Workflow_Yields_Exactly_One_Result()
    {
        // Every executor in the inner graph can yield, so a wiring mistake
        // produces two outputs rather than an error — and the outer workflow
        // would then finalize the same return twice.
        Workflow workflow = Program.BuildFindReplacementWorkflow();
        var outputs = new List<ReplacementResult>();

        await using StreamingRun run = await InProcessExecution
            .RunStreamingAsync(workflow, new ReplacementRequest("R-1", "sku-mug-red"));

        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            if (evt is WorkflowOutputEvent { Data: ReplacementResult r })
            {
                outputs.Add(r);
            }
        }

        outputs.Should().ContainSingle();
    }

    [Fact]
    public void Each_Build_Returns_A_Fresh_Workflow_Instance()
    {
        // The factory contract. Sharing one Workflow shares its executor
        // instances, and the resulting state corruption is silent — so this is
        // pinned rather than left to the comment.
        Program.BuildFindReplacementWorkflow().Should()
            .NotBeSameAs(Program.BuildFindReplacementWorkflow());
    }

    // ─────────────── The same workflow, nested ───────────────

    [Fact]
    public async Task The_Outer_Workflow_Approves_Through_The_Nested_One()
    {
        IReadOnlyList<string> outputs = await Program.RunProcessReturnAsync("R-1001", "sku-mug-red");

        outputs.Should().ContainSingle();
        outputs[0].Should().Be(
            "Return R-1001: replacement sku-mug-red approved and shipped (in stock: Ceramic Mug — Red).");
    }

    [Fact]
    public async Task The_Outer_Workflow_Turns_A_Rejection_Into_A_Refund()
    {
        IReadOnlyList<string> outputs = await Program.RunProcessReturnAsync("R-1002", "sku-mug-blue");

        outputs[0].Should().Contain("rejected (out of stock)").And.Contain("issuing a refund instead");
    }

    [Fact]
    public async Task An_Unknown_Sku_Reaches_The_Outer_Workflow_With_Its_Own_Reason()
    {
        // Proves the inner workflow's reason string survives the boundary
        // rather than being flattened to a bare pass/fail.
        IReadOnlyList<string> outputs = await Program.RunProcessReturnAsync("R-1003", "sku-unknown");

        outputs[0].Should().Contain("not found in catalog");
    }

    [Fact]
    public async Task The_Subworkflows_Output_Is_Reshaped_Rather_Than_Yielded_Directly()
    {
        // The property that makes nesting worth anything. The inner workflow
        // yields a ReplacementResult; the outer one yields a string. If the
        // inner output became the outer terminal output, finalize_return would
        // be skipped and the nesting would buy nothing over inlining.
        Workflow workflow = Program.BuildProcessReturnWorkflow();
        var outputs = new List<object?>();

        await using StreamingRun run = await InProcessExecution
            .RunStreamingAsync(workflow, new ReturnRequest("R-1001", "sku-mug-red"));

        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            if (evt is WorkflowOutputEvent output)
            {
                outputs.Add(output.Data);
            }
        }

        outputs.Should().ContainSingle().Which.Should().BeOfType<string>();
        outputs.Should().NotContain(o => o is ReplacementResult);
    }

    [Fact]
    public async Task The_Nested_And_Standalone_Paths_Agree()
    {
        // Reusability, stated as an assertion: the same input must produce the
        // same verdict whether the inner workflow runs alone or as a node.
        foreach (string sku in new[] { "sku-mug-red", "sku-mug-blue", "sku-unknown" })
        {
            ReplacementResult? standalone = await Program.RunFindReplacementAsync("R-9", sku);
            IReadOnlyList<string> nested = await Program.RunProcessReturnAsync("R-9", sku);

            nested[0].Should().Contain(standalone!.Approved ? "approved" : "rejected");
            nested[0].Should().Contain(standalone.Reason);
        }
    }

    [Fact]
    public async Task Two_Runs_Of_The_Outer_Workflow_Do_Not_Interfere()
    {
        // Each run builds its own graph, including its own inner instance. If
        // that ever changed, the second run would inherit the first's executor
        // state — which is precisely the failure the factory exists to prevent.
        IReadOnlyList<string> first = await Program.RunProcessReturnAsync("R-A", "sku-mug-red");
        IReadOnlyList<string> second = await Program.RunProcessReturnAsync("R-B", "sku-plate-green");

        first[0].Should().Contain("R-A").And.Contain("Ceramic Mug — Red");
        second[0].Should().Contain("R-B").And.Contain("Dinner Plate — Green");
    }

    // ─────────────── Data sanity ───────────────

    [Fact]
    public void Every_Catalogue_Product_Has_A_Stock_Entry()
    {
        // Otherwise a catalogue product would fall through to a default of 0
        // and report "out of stock" for a reason nobody intended.
        Program.Catalog.Keys.Should().BeSubsetOf(Program.Stock.Keys);
    }

    [Fact]
    public void The_Fixture_Covers_Both_The_In_Stock_And_Out_Of_Stock_Cases()
    {
        // Guards the demo data. If someone restocked sku-mug-blue, scenario 2
        // would quietly stop demonstrating anything.
        Program.Stock.Values.Should().Contain(v => v > 0);
        Program.Stock.Values.Should().Contain(v => v == 0);
    }
}
