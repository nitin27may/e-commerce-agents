using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using FluentAssertions;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using System.ComponentModel;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// The fixture-replaying chat client that makes a .NET eval suite deterministic and free.
/// </summary>
/// <remarks>
/// The bar here is not "returns canned text" — <c>FakeChatClient</c> already does that, by
/// popping strings off a queue. The bar is <b>replaying a tool-calling loop</b>: the model
/// asks for a tool, the local tool really runs, and the follow-up turn finds its own
/// recording. A client keyed on call order cannot do that, because the number of turns
/// varies with what the tools return. That is why this one keys on the request.
/// </remarks>
public sealed class ReplayChatClientTests : IDisposable
{
    private readonly string _dir = Path.Combine(
        Path.GetTempPath(), "replay-tests-" + Guid.NewGuid().ToString("N"));

    public void Dispose()
    {
        if (Directory.Exists(_dir))
        {
            Directory.Delete(_dir, recursive: true);
        }
    }

    [Fact]
    public async Task ARecordedAnswerIsReplayed()
    {
        var client = new ReplayChatClient(_dir);
        var messages = new[] { new ChatMessage(ChatRole.User, "what is the return window?") };

        await client.RecordAsync(messages, null, new ChatResponse(
            new ChatMessage(ChatRole.Assistant, "30 days from delivery.")));

        var replayed = await client.GetResponseAsync(messages);

        replayed.Text.Should().Be("30 days from delivery.");
    }

    [Fact]
    public async Task AMissingFixtureIsItsOwnError_NotAWrongAnswer()
    {
        var client = new ReplayChatClient(_dir);

        var act = async () => await client.GetResponseAsync(
            [new ChatMessage(ChatRole.User, "never recorded")]);

        // Python scored missing fixtures as all-zero cases, so CI reported "this agent
        // scores 40%" when the truth was "it never ran". A distinct exception is what
        // lets a runner tell those apart.
        await act.Should().ThrowAsync<ReplayChatClient.FixtureMissingException>()
            .WithMessage("*did not run at all*");
    }

    [Fact]
    public async Task DifferentQuestionsGetDifferentFixtures()
    {
        var client = new ReplayChatClient(_dir);
        await client.RecordAsync([new ChatMessage(ChatRole.User, "a")], null,
            new ChatResponse(new ChatMessage(ChatRole.Assistant, "answer-a")));
        await client.RecordAsync([new ChatMessage(ChatRole.User, "b")], null,
            new ChatResponse(new ChatMessage(ChatRole.Assistant, "answer-b")));

        (await client.GetResponseAsync([new ChatMessage(ChatRole.User, "a")])).Text.Should().Be("answer-a");
        (await client.GetResponseAsync([new ChatMessage(ChatRole.User, "b")])).Text.Should().Be("answer-b");
    }

    [Fact]
    public async Task VolatileIdsAndTimestampsInToolResults_DoNotChangeTheKey()
    {
        var client = new ReplayChatClient(_dir);

        // A tool result carries live database payloads back into the next turn's messages.
        // Reseed the database and every id and timestamp changes — so without scrubbing,
        // the whole corpus misses and the suite reports a quality collapse that is really
        // a hashing artifact. This is the exact failure Python's issue #25 chased.
        ChatMessage[] Turn(string uuid, string ts) =>
        [
            new ChatMessage(ChatRole.User, "find my order"),
            new ChatMessage(ChatRole.Tool, $"{{\"order_id\":\"{uuid}\",\"placed_at\":\"{ts}\"}}"),
        ];

        await client.RecordAsync(
            Turn("dc9e2baa-8182-58f8-9df1-97d049426ba1", "2026-08-01T10:00:00Z"), null,
            new ChatResponse(new ChatMessage(ChatRole.Assistant, "Found it.")));

        var afterReseed = await client.GetResponseAsync(
            Turn("035ae219-49be-59e2-a71e-94f06ca0fec9", "2026-08-21T13:45:12.331Z"));

        afterReseed.Text.Should().Be("Found it.");
    }

    [Fact]
    public async Task AVolatileLookingValueInAUserMessage_StillChangesTheKey()
    {
        var client = new ReplayChatClient(_dir);
        await client.RecordAsync(
            [new ChatMessage(ChatRole.User, "track dc9e2baa-8182-58f8-9df1-97d049426ba1")], null,
            new ChatResponse(new ChatMessage(ChatRole.Assistant, "first")));

        // Scrubbing is confined to tool results on purpose. Two genuinely different
        // questions must not collapse onto one recording just because both contain a UUID.
        var act = async () => await client.GetResponseAsync(
            [new ChatMessage(ChatRole.User, "track 035ae219-49be-59e2-a71e-94f06ca0fec9")]);

        await act.Should().ThrowAsync<ReplayChatClient.FixtureMissingException>();
    }

    [Fact]
    public async Task ARecordedToolCall_ActuallyRunsTheLocalToolAndCompletesTheLoop()
    {
        var dir = _dir;
        var client = new ReplayChatClient(dir);
        var calls = 0;

        [Description("Look up the price of a product")]
        string GetPrice([Description("product name")] string name)
        {
            calls++;
            return "299.99";
        }

        var tool = AIFunctionFactory.Create(GetPrice, nameof(GetPrice));
        var options = new ChatOptions { Tools = [tool], Instructions = "You are a shop assistant." };
        var first = new[] { new ChatMessage(ChatRole.User, "how much are the headphones?") };

        // Turn 1: the model asks for the tool.
        await client.RecordAsync(first, options, new ChatResponse(
            new ChatMessage(ChatRole.Assistant, [new FunctionCallContent("call_0", nameof(GetPrice),
                new Dictionary<string, object?> { ["name"] = "headphones" })])));

        // Turn 2: having seen the tool's result, it answers. Recorded under the messages
        // that will exist *after* the tool ran — which is what makes this a loop rather
        // than a two-item playlist.
        var afterTool = first
            .Append(new ChatMessage(ChatRole.Assistant, [new FunctionCallContent("call_0", nameof(GetPrice),
                new Dictionary<string, object?> { ["name"] = "headphones" })]))
            .Append(new ChatMessage(ChatRole.Tool, [new FunctionResultContent("call_0", "299.99")]))
            .ToArray();

        await client.RecordAsync(afterTool, options, new ChatResponse(
            new ChatMessage(ChatRole.Assistant, "They're $299.99.")));

        var agent = client.AsAIAgent(instructions: "You are a shop assistant.", tools: [tool]);
        var reply = await agent.RunAsync("how much are the headphones?");

        calls.Should().Be(1, "the local tool must genuinely execute, not be replayed as text");
        reply.Text.Should().Contain("299.99");
    }

    [Fact]
    public void TheFactoryBuildsAReplayClient_AndRejectsAnUnknownProvider()
    {
        ChatClientFactory.Create(new AgentSettings { LlmProvider = "replay", ReplayFixturesDir = _dir })
            .Should().BeOfType<ReplayChatClient>();

        // Previously an unknown provider fell through to OpenAI and failed with
        // "OPENAI_API_KEY is required", which says nothing about the real mistake.
        var act = () => ChatClientFactory.Create(new AgentSettings { LlmProvider = "gemini" });
        act.Should().Throw<InvalidOperationException>().WithMessage("*not supported*");
    }
}
