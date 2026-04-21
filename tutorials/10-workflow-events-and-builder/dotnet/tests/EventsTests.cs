// MAF v1 — Chapter 10 tests (Workflow Events and Builder)
//
// No LLM — these executors are deterministic so we can assert exactly
// on the event stream. Mirrors the Python test suite at
// tutorials/10-workflow-events-and-builder/python/tests/test_events.py.

using FluentAssertions;
using Microsoft.Agents.AI.Workflows;
using Xunit;

namespace MafV1.Ch10.Events.Tests;

public sealed class EventsTests
{
    [Fact]
    public async Task Progress_Events_Emit_In_Pipeline_Order_On_Happy_Path()
    {
        var progress = await CollectProgress("hello world");

        progress.Select(p => p.Step).Should().Equal("uppercase", "validate", "log");
        progress.Select(p => p.Percent).Should().Equal(33, 66, 100);
    }

    [Fact]
    public async Task Progress_Events_Carry_Structured_Payload()
    {
        var progress = await CollectProgress("hello");

        progress.Should().AllBeOfType<ProgressEvent>();
        progress.Should().AllSatisfy(p =>
        {
            p.Step.Should().NotBeNullOrWhiteSpace();
            p.Percent.Should().BeInRange(0, 100);
        });
    }

    [Fact]
    public async Task Empty_Input_Short_Circuits_Before_Log_Emits_Progress()
    {
        var (progress, outputs) = await Collect(string.Empty);

        progress.Select(p => p.Step).Should().Contain("uppercase").And.Contain("validate");
        progress.Select(p => p.Step).Should().NotContain("log", "log must not run when validate short-circuits");
        outputs.Should().ContainSingle().Which.Should().Be("[skipped: empty input]");
    }

    [Fact]
    public async Task Workflow_Output_Follows_Final_Progress_Event()
    {
        var (progress, outputs) = await Collect("hi");

        outputs.Should().ContainSingle().Which.Should().Be("LOGGED: HI");
        progress.Last().Percent.Should().Be(100);
    }

    [Fact]
    public async Task Lifecycle_Events_Interleave_With_Custom_Events_In_Arrival_Order()
    {
        // This locks in the defining property of this chapter: MAF's lifecycle
        // events and the executor's custom events flow through the same stream
        // in the order they occurred.
        var workflow = WorkflowFactory.Build();
        var trail = new List<string>();

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, "mix");
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            switch (evt)
            {
                case ProgressEvent p:
                    trail.Add($"custom:{p.Step}");
                    break;
                case ExecutorInvokedEvent i:
                    trail.Add($"invoked:{i.ExecutorId}");
                    break;
                case WorkflowOutputEvent:
                    trail.Add("output");
                    break;
            }
        }

        // Uppercase: invoked fires before its custom progress event,
        // which fires before the next executor's invoked event.
        trail.IndexOf("invoked:uppercase").Should().BeLessThan(trail.IndexOf("custom:uppercase"));
        trail.IndexOf("custom:uppercase").Should().BeLessThan(trail.IndexOf("invoked:validate"));
        trail.IndexOf("invoked:validate").Should().BeLessThan(trail.IndexOf("custom:validate"));
        trail.IndexOf("custom:validate").Should().BeLessThan(trail.IndexOf("invoked:log"));
        trail.IndexOf("invoked:log").Should().BeLessThan(trail.IndexOf("custom:log"));
        trail.Last().Should().Be("output", "the final workflow output arrives after all progress events");
    }

    [Fact]
    public async Task Stream_Yields_Events_Before_Returning_Final_Output()
    {
        // Progress must stream — not be buffered and delivered in one batch
        // after completion. Assert by checking the output arrives strictly
        // after the final progress event.
        var workflow = WorkflowFactory.Build();
        var order = new List<string>();

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, "stream-test");
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            if (evt is ProgressEvent p)
            {
                order.Add($"progress:{p.Step}");
            }
            else if (evt is WorkflowOutputEvent)
            {
                order.Add("output");
            }
        }

        order.Last().Should().Be("output");
        order.IndexOf("progress:log").Should().BeLessThan(order.IndexOf("output"));
    }

    // ─────────────── helpers ───────────────

    private static async Task<List<ProgressEvent>> CollectProgress(string input)
    {
        var (progress, _) = await Collect(input);
        return progress;
    }

    private static async Task<(List<ProgressEvent> Progress, List<string> Outputs)> Collect(string input)
    {
        var workflow = WorkflowFactory.Build();
        var progress = new List<ProgressEvent>();
        var outputs = new List<string>();

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, input);
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            switch (evt)
            {
                case ProgressEvent p:
                    progress.Add(p);
                    break;
                case WorkflowOutputEvent o when o.Data is string s:
                    outputs.Add(s);
                    break;
            }
        }

        return (progress, outputs);
    }
}
