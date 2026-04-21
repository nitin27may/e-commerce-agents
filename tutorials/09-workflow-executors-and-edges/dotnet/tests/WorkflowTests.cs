// MAF v1 — Chapter 09 tests (Workflow Executors and Edges)
//
// No LLM — these executors are deterministic so we can assert exactly
// on the final workflow outputs. Mirrors the Python test suite at
// tutorials/09-workflow-executors-and-edges/python/tests/test_workflow.py.

using FluentAssertions;
using Microsoft.Agents.AI.Workflows;
using Xunit;

namespace MafV1.Ch09.WorkflowDemo.Tests;

public sealed class WorkflowTests
{
    [Fact]
    public async Task Happy_Path_Pipeline_Returns_Uppercased_Logged_Output()
    {
        var outputs = await RunAndCollect("hello world");
        outputs.Should().ContainSingle().Which.Should().Be("LOGGED: HELLO WORLD");
    }

    [Fact]
    public async Task Empty_Input_Short_Circuits_At_Validate_Executor()
    {
        var outputs = await RunAndCollect(string.Empty);
        outputs.Should().ContainSingle().Which.Should().Be("[skipped: empty input]");
    }

    [Fact]
    public async Task Whitespace_Only_Input_Treated_As_Empty()
    {
        var outputs = await RunAndCollect("   ");
        outputs.Should().ContainSingle().Which.Should().Be("[skipped: empty input]");
    }

    [Fact]
    public async Task Workflow_Builds_Successfully_With_All_Three_Executors()
    {
        // Act
        var workflow = WorkflowFactory.Build();

        // Assert — a non-null build means WorkflowBuilder accepted the edges
        // and source-generated protocol config for every `[MessageHandler]`.
        workflow.Should().NotBeNull();
    }

    [Fact]
    public async Task Event_Stream_Fires_All_Three_Executors_In_Order_On_Happy_Path()
    {
        var workflow = WorkflowFactory.Build();
        var invokedOrder = new List<string>();

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, "pipeline-test");
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            if (evt is ExecutorInvokedEvent invoked)
            {
                invokedOrder.Add(invoked.ExecutorId);
            }
        }

        invokedOrder.Should().ContainInOrder("uppercase", "validate", "log");
    }

    [Fact]
    public async Task Event_Stream_Skips_Log_Executor_When_Validate_Short_Circuits()
    {
        var workflow = WorkflowFactory.Build();
        var invokedOrder = new List<string>();

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, string.Empty);
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            if (evt is ExecutorInvokedEvent invoked)
            {
                invokedOrder.Add(invoked.ExecutorId);
            }
        }

        invokedOrder.Should().Contain("uppercase").And.Contain("validate");
        invokedOrder.Should().NotContain("log");
    }

    // ─────────────── helpers ───────────────

    private static async Task<List<string>> RunAndCollect(string input)
    {
        var workflow = WorkflowFactory.Build();
        var outputs = new List<string>();

        await foreach (string s in WorkflowRunner.RunAsync(workflow, input))
        {
            outputs.Add(s);
        }

        return outputs;
    }
}
