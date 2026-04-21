// MAF v1 — Chapter 11 tests (Agents in Workflows)
//
// Two kinds of tests:
//
//   * Wiring tests — build the workflow against a fake IChatClient that
//     returns deterministic text, then assert on the event stream (which
//     executors ran, in what order, what the final output was). These run
//     in milliseconds with no network.
//
//   * Integration tests — hit a real LLM to verify the chain actually
//     translates. Skipped unless OPENAI_API_KEY or Azure OpenAI creds are
//     present in the environment.
//
// The wiring tests are what you'd run on every PR; the integration tests
// go in a nightly / smoke suite.

using System.Runtime.CompilerServices;
using FluentAssertions;
using Microsoft.Agents.AI;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Extensions.AI;
using Xunit;

namespace MafV1.Ch11.AgentsInWorkflows.Tests;

public sealed class AgentsInWorkflowsTests
{
    // ─────────────── Wiring tests (no LLM) ───────────────

    [Fact]
    public async Task Manual_Workflow_Emits_Final_String_Output()
    {
        var fake = new ScriptedChatClient("BONJOUR", "HOLA");

        var output = await ManualAdapterWorkflow.RunAsync(fake, "hello");

        output.Should().Be("HOLA");
    }

    [Fact]
    public async Task Manual_Workflow_Fires_Both_Agent_Executors_In_Order()
    {
        var fake = new ScriptedChatClient("BONJOUR", "HOLA");
        var invoked = new List<string>();
        var translations = new List<string>();

        var inputAdapter = new InputAdapter();
        var frenchExecutor = new TranslationAgentExecutor("en-to-fr", fake, "French");
        var spanishExecutor = new TranslationAgentExecutor("fr-to-es", fake, "Spanish");
        var outputAdapter = new OutputAdapter();

        Workflow workflow = new WorkflowBuilder(inputAdapter)
            .AddEdge(inputAdapter, frenchExecutor)
            .AddEdge(frenchExecutor, spanishExecutor)
            .AddEdge(spanishExecutor, outputAdapter)
            .WithOutputFrom(outputAdapter)
            .Build();

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, "hello");
        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            switch (evt)
            {
                case ExecutorInvokedEvent inv:
                    invoked.Add(inv.ExecutorId);
                    break;
                case TranslationCompletedEvent t:
                    translations.Add($"{t.ExecutorId}:{t.Text}");
                    break;
            }
        }

        invoked.Should().ContainInOrder("input-adapter", "en-to-fr", "fr-to-es", "output-adapter");
        translations.Should().Equal("en-to-fr:BONJOUR", "fr-to-es:HOLA");
    }

    [Fact]
    public async Task Manual_Workflow_Surfaces_Executor_Failures()
    {
        var fake = new ScriptedChatClient(throwOnFirst: true);

        var act = async () => await ManualAdapterWorkflow.RunAsync(fake, "hello");

        await act.Should().ThrowAsync<InvalidOperationException>()
            .WithMessage("Executor 'en-to-fr' failed*");
    }

    [Fact]
    public async Task Manual_Workflow_Builds_With_All_Four_Executors()
    {
        var fake = new ScriptedChatClient("x", "y");

        // .NET's WorkflowBuilder requires one instance per executor id —
        // reusing the same variable across AddEdge calls is the idiomatic
        // pattern. Each call to `new InputAdapter()` would be rejected.
        var inputAdapter = new InputAdapter();
        var frenchExecutor = new TranslationAgentExecutor("en-to-fr", fake, "French");
        var spanishExecutor = new TranslationAgentExecutor("fr-to-es", fake, "Spanish");
        var outputAdapter = new OutputAdapter();

        var workflow = new WorkflowBuilder(inputAdapter)
            .AddEdge(inputAdapter, frenchExecutor)
            .AddEdge(frenchExecutor, spanishExecutor)
            .AddEdge(spanishExecutor, outputAdapter)
            .WithOutputFrom(outputAdapter)
            .Build();

        workflow.Should().NotBeNull();
        await Task.CompletedTask;
    }

    [Fact]
    public async Task Sequential_BuildSequential_Composes_A_Runnable_Workflow()
    {
        var fake = new ScriptedChatClient("BONJOUR", "HOLA");
        AIAgent a1 = Program.TranslationAgent(fake, "French", id: "en-to-fr");
        AIAgent a2 = Program.TranslationAgent(fake, "Spanish", id: "fr-to-es");

        Workflow workflow = AgentWorkflowBuilder.BuildSequential(a1, a2);

        workflow.Should().NotBeNull();
    }

    // ─────────────── Fake IChatClient ───────────────
    //
    // Returns pre-scripted responses in order. No network, deterministic.

    private sealed class ScriptedChatClient : IChatClient
    {
        private readonly Queue<string> _responses;
        private readonly bool _throwOnFirst;

        public ScriptedChatClient(params string[] responses)
        {
            _responses = new Queue<string>(responses);
            _throwOnFirst = false;
        }

        public ScriptedChatClient(bool throwOnFirst)
        {
            _responses = new Queue<string>();
            _throwOnFirst = throwOnFirst;
        }

        public Task<ChatResponse> GetResponseAsync(
            IEnumerable<ChatMessage> messages,
            ChatOptions? options = null,
            CancellationToken cancellationToken = default)
        {
            if (_throwOnFirst)
            {
                throw new InvalidOperationException("scripted failure");
            }

            var text = _responses.Count > 0 ? _responses.Dequeue() : string.Empty;
            var response = new ChatResponse(new ChatMessage(ChatRole.Assistant, text));
            return Task.FromResult(response);
        }

        public async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
            IEnumerable<ChatMessage> messages,
            ChatOptions? options = null,
            [EnumeratorCancellation] CancellationToken cancellationToken = default)
        {
            if (_throwOnFirst)
            {
                throw new InvalidOperationException("scripted failure");
            }

            var text = _responses.Count > 0 ? _responses.Dequeue() : string.Empty;
            yield return new ChatResponseUpdate(ChatRole.Assistant, text);
            await Task.CompletedTask;
        }

        public object? GetService(Type serviceType, object? serviceKey = null) => null;

        public void Dispose() { }
    }
}
