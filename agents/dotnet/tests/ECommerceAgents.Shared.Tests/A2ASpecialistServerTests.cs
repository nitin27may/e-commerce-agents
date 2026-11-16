using System.Net;
using System.Text;
using System.Text.Json;
using A2A;
using ECommerceAgents.Shared.A2A.Protocol;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;
using static ECommerceAgents.Shared.Tests.A2ASupport;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// B-13: a .NET specialist speaks A2A 1.0, checked with the official A2A .NET SDK client.
/// A real specialist host (<c>AgentHost.Build</c>: real auth middleware, step recorder, legacy routes) runs in
/// memory around a scripted model; the SDK client is pointed at it the way any third-party client would be.
/// Mirrors the Python <c>tests/test_a2a_conformance.py</c> case for case, because the two stacks share one
/// wire contract (docs/a2a.md).
/// </summary>
public sealed class A2ASpecialistServerTests
{
    // ─────────────────────── the agent card ───────────────────────

    [Fact]
    public async Task Card_IsPublic_AndParsesWithTheOfficialResolver()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var http = specialist.Caller(credential: false); // no credential at all

        var card = await new A2ACardResolver(new Uri("http://localhost"), http).GetAgentCardAsync();

        card.Name.Should().Be("product-discovery");
        card.Version.Should().Be(A2AWire.AgentVersion);
        card.SupportedInterfaces.Should().ContainSingle();
        var iface = card.SupportedInterfaces[0];
        iface.Url.Should().Be("http://localhost/a2a/v1");
        iface.ProtocolBinding.Should().Be("JSONRPC");
        iface.ProtocolVersion.Should().Be("1.0");
        card.Capabilities.Streaming.Should().BeTrue();
        card.Capabilities.PushNotifications.Should().BeFalse();
        card.Capabilities.Extensions!.Select(e => e.Uri).Should().BeEquivalentTo(A2AWire.AllExtensionUris);
        card.Capabilities.Extensions!.Should().OnlyContain(e => e.Required != true);
    }

    [Fact]
    public async Task Card_SkillsComeFromTheToolsNameAndDescription_Verbatim()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var card = await new A2ACardResolver(new Uri("http://localhost"), specialist.Caller(credential: false)).GetAgentCardAsync();

        var lookup = card.Skills.Single(s => s.Id == "lookup");
        lookup.Name.Should().Be("lookup");
        lookup.Description.Should().Be("Look up the current price of a product by SKU.");
        card.Skills.Select(s => s.Id).Should().Contain("stock");
    }

    [Fact]
    public async Task Card_HidesToolsTheLeastPrivilegedRoleCannotCall()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var card = await new A2ACardResolver(new Uri("http://localhost"), specialist.Caller(credential: false)).GetAgentCardAsync();

        // adjust_price is [RequiresRole("seller", "admin")]: an anonymous reader of a public card must not see it.
        card.Skills.Select(s => s.Id).Should().NotContain("adjust_price");
        ToJson(card).Should().NotContain("Adjust a product's price");
    }

    private static string ToJson(AgentCard card) => SpecialistAgentCard.ToJson(card);

    [Fact]
    public async Task Card_DeclaresHowToAuthenticate()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var card = await new A2ACardResolver(new Uri("http://localhost"), specialist.Caller(credential: false)).GetAgentCardAsync();

        card.SecuritySchemes!.Keys.Should().BeEquivalentTo("agentSecret", "serviceToken");
        card.SecuritySchemes["agentSecret"].ApiKeySecurityScheme!.Name.Should().Be("X-Agent-Secret");
        card.SecuritySchemes["serviceToken"].HttpAuthSecurityScheme!.Scheme.Should().Be("Bearer");
        card.SecurityRequirements.Should().HaveCount(2);
    }

    [Fact]
    public async Task Card_PublicBaseUrlOverridesTheDerivedOne()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(
            env: new Dictionary<string, string> { ["A2A_PUBLIC_BASE_URL"] = "https://agents.example.com/" });
        var card = await new A2ACardResolver(new Uri("http://localhost"), specialist.Caller(credential: false)).GetAgentCardAsync();

        card.SupportedInterfaces[0].Url.Should().Be("https://agents.example.com/a2a/v1");
    }

    // ─────────────────────── blocking SendMessage ───────────────────────

    [Fact]
    public async Task BlockingSend_CompletesATask_WithTheAnswerAsAnArtifact()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var client = Sdk(specialist.Caller());

        var response = await client.SendMessageAsync(Request());

        response.Task.Should().NotBeNull();
        response.Task!.Status.State.Should().Be(TaskState.Completed);
        Text(response.Task).Should().Be(ScriptedToolChatClient.Answer);
        response.Task.Artifacts.Should().ContainSingle().Which.Name.Should().Be("answer");
    }

    [Fact]
    public async Task BlockingResponse_CarriesStepsAndUsage_InTaskMetadata_WhenAsked()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var client = Sdk(specialist.Caller(extensions: true));

        var task = (await client.SendMessageAsync(Request())).Task!;

        var steps = Meta(task.Metadata, A2AWire.StepsExtensionUri);
        steps.GetProperty("offset").GetInt32().Should().Be(0);
        var step = steps.GetProperty("steps").EnumerateArray().Single();
        step.GetProperty("tool_name").GetString().Should().Be("lookup");
        step.GetProperty("agent").GetString().Should().Be("product-discovery");
        step.GetProperty("status").GetString().Should().NotBeNullOrEmpty();
        step.GetProperty("tool_output").ToString().Should().Contain("XM5=79.99 for alice@example.com");

        var usage = Meta(task.Metadata, A2AWire.UsageExtensionUri);
        usage.GetProperty("input_token_count").GetInt64().Should().Be(5);
        usage.GetProperty("output_token_count").GetInt64().Should().Be(2);
        usage.GetProperty("cached_input_token_count").GetInt64().Should().Be(0);
        usage.GetProperty("model").GetString().Should().NotBeNullOrEmpty();
    }

    [Fact]
    public async Task ExtensionMetadata_IsOnlySent_WhenTheCallerAskedForIt()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();

        var plain = (await Sdk(specialist.Caller()).SendMessageAsync(Request())).Task!;
        plain.Metadata.Should().BeNullOrEmpty();

        var streamed = await StreamAsync(Sdk(specialist.Caller()), Request());
        streamed.Where(e => e.StatusUpdate is not null).Should().OnlyContain(e => e.StatusUpdate!.Metadata == null || e.StatusUpdate.Metadata.Count == 0);
    }

    [Fact]
    public async Task ActivatedExtensions_AreEchoedInTheResponseHeader()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var http = specialist.Caller(extensions: true);

        var response = await http.PostAsync(A2AWire.RpcPath, RpcBody("GetTask", new { id = "nope" }));

        response.Headers.TryGetValues("A2A-Extensions", out var values).Should().BeTrue();
        string.Join(",", values!).Split(',').Select(v => v.Trim()).Should().BeEquivalentTo(A2AWire.AllExtensionUris);
    }

    // ─────────────────────── streaming ───────────────────────

    [Fact]
    public async Task Streaming_WalksSubmittedWorkingCompleted_AndEndsOnATerminalState()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();

        var events = await StreamAsync(Sdk(specialist.Caller()), Request());

        events[0].Task.Should().NotBeNull();
        events[0].Task!.Status.State.Should().Be(TaskState.Submitted);
        var states = events.Where(e => e.StatusUpdate is not null).Select(e => e.StatusUpdate!.Status.State).ToList();
        states.Should().StartWith(TaskState.Working);
        states[^1].Should().Be(TaskState.Completed);
        states.Count(s => s is TaskState.Completed or TaskState.Failed).Should().Be(1, "exactly one terminal update");
        events[^1].StatusUpdate!.Status.State.Should().Be(TaskState.Completed);
    }

    [Fact]
    public async Task StreamedText_ArrivesAsAppendChunks_WithAFinalLastChunk()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();

        var chunks = (await StreamAsync(Sdk(specialist.Caller()), Request()))
            .Where(e => e.ArtifactUpdate is not null).Select(e => e.ArtifactUpdate!).ToList();

        chunks.Should().HaveCountGreaterThan(1);
        chunks.Select(c => c.Artifact.ArtifactId).Distinct().Should().ContainSingle();
        chunks[0].Append.Should().BeFalse("the SDK rejects append=true for an artifact that does not exist");
        chunks.Skip(1).Should().OnlyContain(c => c.Append);
        chunks[^1].LastChunk.Should().BeTrue();
        chunks.Take(chunks.Count - 1).Should().OnlyContain(c => !c.LastChunk);
        string.Concat(chunks.SelectMany(c => c.Artifact.Parts).Select(p => p.Text)).Should().Be(ScriptedToolChatClient.Answer);
    }

    [Fact]
    public async Task StreamedSteps_AndUsage_RideInStatusUpdateMetadata()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();

        var updates = (await StreamAsync(Sdk(specialist.Caller(extensions: true)), Request()))
            .Where(e => e.StatusUpdate is not null).Select(e => e.StatusUpdate!).ToList();

        var stepUpdates = updates.Where(u => u.Metadata?.ContainsKey(A2AWire.StepsExtensionUri) == true).ToList();
        stepUpdates.Should().NotBeEmpty();
        // Live updates carry what is new; the terminal one repeats everything from offset 0.
        var terminal = updates[^1];
        terminal.Status.State.Should().Be(TaskState.Completed);
        Meta(terminal.Metadata, A2AWire.StepsExtensionUri).GetProperty("offset").GetInt32().Should().Be(0);
        Meta(terminal.Metadata, A2AWire.UsageExtensionUri).GetProperty("input_token_count").GetInt64().Should().Be(5);

        var known = new List<A2AWire.WireStep>();
        foreach (var u in stepUpdates)
        {
            A2AWire.MergeSteps(known, Meta(u.Metadata, A2AWire.StepsExtensionUri));
        }

        known.Should().ContainSingle().Which.ToolName.Should().Be("lookup");
    }

    [Fact]
    public async Task Text_SurvivesEveryHop_EvenTheAwkwardCharacters()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(mode: "weird");
        var client = Sdk(specialist.Caller());

        var streamed = string.Concat((await StreamAsync(client, Request()))
            .Where(e => e.ArtifactUpdate is not null).SelectMany(e => e.ArtifactUpdate!.Artifact.Parts).Select(p => p.Text));
        var blocking = Text((await client.SendMessageAsync(Request())).Task!);

        const string expected = "a\u2028b\u2029c\u0085d line\r\nbreak \U0001F600";
        streamed.Should().Be(expected);
        blocking.Should().Be(expected);
    }

    [Fact]
    public async Task SseBody_NeverCarriesARawLineSeparator()
    {
        // The Python SDK client reads an SSE body with str.splitlines(), which breaks on U+2028, U+2029 and U+0085;
        // SSE itself does not. They must travel escaped (a JSON string holds the same value either way).
        await using var specialist = await ScriptedSpecialist.StartAsync(mode: "weird");
        var http = specialist.Caller();

        var response = await http.PostAsync(A2AWire.RpcPath, RpcBody("SendStreamingMessage", Request()));
        var body = await response.Content.ReadAsStringAsync();

        body.Should().Contain("data:");
        body.Should().NotContain("\u2028").And.NotContain("\u2029").And.NotContain("\u0085");
    }

    // ─────────────────────── model input, sessions ───────────────────────

    [Fact]
    public async Task TheModelSeesTheSameMessages_OnBothWires()
    {
        await using var legacy = await ScriptedSpecialist.StartAsync();
        await using var a2a = await ScriptedSpecialist.StartAsync();

        var legacyResponse = await legacy.Caller().PostAsync(
            "/message:send", new StringContent("{\"message\":\"price of the XM5?\"}", Encoding.UTF8, "application/json"));
        legacyResponse.EnsureSuccessStatusCode();
        await Sdk(a2a.Caller()).SendMessageAsync(Request("price of the XM5?"));

        a2a.Model.Seen.Should().BeEquivalentTo(legacy.Model.Seen, o => o.WithStrictOrdering());
        a2a.Model.Seen.Should().HaveCount(2, "the tool call round and the answer round");
    }

    [Fact]
    public async Task AServerGeneratedContextId_IsNeverUsedAsASession()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();

        // No contextId from the caller: the SDK invents a GUID. It must not be mistaken for a session id.
        var task = (await Sdk(specialist.Caller()).SendMessageAsync(Request())).Task!;

        task.ContextId.Should().NotBeNullOrEmpty();
        Guid.TryParse(task.ContextId, out _).Should().BeTrue("the SDK's invented id looks exactly like a session id");
        A2AWire.SessionIdFromContext(task.ContextId, clientProvided: false, headerSessionId: "").Should().BeEmpty();
    }

    [Theory]
    [InlineData(ScriptedSpecialist.Session, true, "", ScriptedSpecialist.Session)]
    [InlineData("6F1C0A5E2D3B4C7E9A101B2C3D4E5F60", true, "", ScriptedSpecialist.Session)]
    [InlineData("6f1c0a5e2d3b4c7e9a101b2c3d4e5f60", false, "", "")]
    [InlineData("not-a-uuid", true, "", "")]
    [InlineData("not-a-uuid", true, ScriptedSpecialist.Session, ScriptedSpecialist.Session)]
    [InlineData(null, true, ScriptedSpecialist.Session, ScriptedSpecialist.Session)]
    [InlineData("", true, "", "")]
    public void SessionIdResolution(string? contextId, bool clientProvided, string header, string expected) =>
        A2AWire.SessionIdFromContext(contextId, clientProvided, header).Should().Be(expected);

    [Fact]
    public async Task AClientProvidedContextId_IsEchoedOnTheTask()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();

        var task = (await Sdk(specialist.Caller()).SendMessageAsync(Request(contextId: ScriptedSpecialist.Session))).Task!;

        task.ContextId.Should().Be(ScriptedSpecialist.Session);
    }

    // ─────────────────────── failures ───────────────────────

    [Fact]
    public async Task AnAgentError_FailsTheTask_WithoutLeakingTheErrorText()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(mode: "boom");

        var task = (await Sdk(specialist.Caller(extensions: true)).SendMessageAsync(Request())).Task!;

        task.Status.State.Should().Be(TaskState.Failed);
        var text = task.Status.Message!.Parts.Single().Text;
        text.Should().Be(A2AWire.FailureText);
        JsonSerializer.Serialize(task, A2AJsonUtilities.DefaultOptions).Should().NotContain("secret_table");
    }

    [Fact]
    public async Task AStreamedFailure_EndsWithAFailedStatusUpdate()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(mode: "boom");

        var events = await StreamAsync(Sdk(specialist.Caller()), Request());

        events[^1].StatusUpdate!.Status.State.Should().Be(TaskState.Failed);
        events[^1].StatusUpdate!.Status.Message!.Parts.Single().Text.Should().Be(A2AWire.FailureText);
    }

    [Fact]
    public async Task AReplayMiss_IsAFailedTask_MarkedWithItsExtension()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(mode: "miss");

        var task = (await Sdk(specialist.Caller(extensions: true)).SendMessageAsync(Request())).Task!;

        task.Status.State.Should().Be(TaskState.Failed);
        Meta(task.Metadata, A2AWire.ReplayMissExtensionUri).GetProperty("error").GetString().Should().Be("replay_miss");
        JsonSerializer.Serialize(task, A2AJsonUtilities.DefaultOptions).Should().NotContain("abc123").And.NotContain("fixtures");
    }

    [Fact]
    public async Task AMessageWithNoText_IsRejected_AndNeverReachesTheModel()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();

        var task = (await Sdk(specialist.Caller()).SendMessageAsync(Request(text: "   "))).Task!;

        task.Status.State.Should().Be(TaskState.Rejected);
        task.Status.Message!.Parts.Single().Text.Should().Be(A2AWire.NoMessageText);
        specialist.Model.Started.Should().Be(0);
    }

    // ─────────────────────── identity, auth, tasks ───────────────────────

    [Fact]
    public async Task TheForwardedUserIdentity_ReachesTheToolsOfTheTask()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();

        var task = (await Sdk(specialist.Caller(email: "bob@example.com", extensions: true)).SendMessageAsync(Request())).Task!;

        var output = Meta(task.Metadata, A2AWire.StepsExtensionUri).GetProperty("steps")[0].GetProperty("tool_output").ToString();
        output.Should().Contain("bob@example.com");
    }

    [Fact]
    public async Task TheA2AEndpoint_RequiresTheInterAgentCredential()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();

        var none = await specialist.Caller(credential: false).PostAsync(A2AWire.RpcPath, RpcBody("SendMessage", Request()));
        none.StatusCode.Should().Be(HttpStatusCode.Unauthorized);

        var wrong = specialist.Caller(credential: false);
        wrong.DefaultRequestHeaders.Add("X-Agent-Secret", "not-the-secret");
        (await wrong.PostAsync(A2AWire.RpcPath, RpcBody("SendMessage", Request()))).StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task GetTask_ReturnsAFinishedTask_ToItsOwnerOnly()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var task = (await Sdk(specialist.Caller()).SendMessageAsync(Request())).Task!;

        var owner = await Sdk(specialist.Caller()).GetTaskAsync(new GetTaskRequest { Id = task.Id });
        owner.Id.Should().Be(task.Id);
        Text(owner).Should().Be(ScriptedToolChatClient.Answer);

        var other = Sdk(specialist.Caller(email: "mallory@example.com"));
        var get = () => other.GetTaskAsync(new GetTaskRequest { Id = task.Id });
        (await get.Should().ThrowAsync<A2AException>()).Which.ErrorCode.Should().Be(A2AErrorCode.TaskNotFound);

        var cancel = () => other.CancelTaskAsync(new CancelTaskRequest { Id = task.Id });
        (await cancel.Should().ThrowAsync<A2AException>()).Which.ErrorCode.Should().Be(A2AErrorCode.TaskNotFound);
    }

    [Fact]
    public async Task AMessageCannotContinueAnotherUsersTask()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var task = (await Sdk(specialist.Caller()).SendMessageAsync(Request())).Task!;

        var send = () => Sdk(specialist.Caller(email: "mallory@example.com"))
            .SendMessageAsync(Request(taskId: task.Id, contextId: task.ContextId));

        (await send.Should().ThrowAsync<A2AException>()).Which.ErrorCode.Should().Be(A2AErrorCode.TaskNotFound);
    }

    [Fact]
    public async Task ListTasks_ShowsOnlyTheCallersOwn()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var mine = (await Sdk(specialist.Caller()).SendMessageAsync(Request())).Task!;
        var theirs = (await Sdk(specialist.Caller(email: "bob@example.com")).SendMessageAsync(Request())).Task!;

        var list = await Sdk(specialist.Caller()).ListTasksAsync(new ListTasksRequest());

        list.Tasks.Select(t => t.Id).Should().Contain(mine.Id).And.NotContain(theirs.Id);
    }

    /// <summary>
    /// Every anonymous caller (an unauthenticated chat forwards an empty email; a health/system call forwards
    /// nothing, stamped <c>system</c>) shares one task owner, so none of them may read a stored task. They are
    /// still served, and the task their own response returns is all they need.
    /// </summary>
    [Theory]
    [InlineData("", "anonymous")]
    [InlineData("system", "system")]
    public async Task AnonymousCallers_CannotReadEachOthersStoredTasks(string email, string role)
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var task = (await Sdk(specialist.Caller(email: email, role: role)).SendMessageAsync(Request())).Task!;
        Text(task).Should().Be(ScriptedToolChatClient.Answer, "an anonymous caller is still served, in its own response");

        var other = Sdk(specialist.Caller(email: email, role: role));
        var get = () => other.GetTaskAsync(new GetTaskRequest { Id = task.Id });
        (await get.Should().ThrowAsync<A2AException>()).Which.ErrorCode.Should().Be(A2AErrorCode.TaskNotFound);

        (await other.ListTasksAsync(new ListTasksRequest())).Tasks.Should().BeEmpty();

        var subscribe = async () =>
        {
            await foreach (var _ in other.SubscribeToTaskAsync(new SubscribeToTaskRequest { Id = task.Id }))
            {
            }
        };
        (await subscribe.Should().ThrowAsync<A2AException>()).Which.ErrorCode.Should().Be(A2AErrorCode.TaskNotFound);

        var @continue = () => other.SendMessageAsync(Request(taskId: task.Id, contextId: task.ContextId));
        (await @continue.Should().ThrowAsync<A2AException>()).Which.ErrorCode.Should().Be(A2AErrorCode.TaskNotFound);

        // A named user's task is no more visible to them than theirs is to a named user.
        var named = (await Sdk(specialist.Caller()).SendMessageAsync(Request())).Task!;
        var readNamed = () => other.GetTaskAsync(new GetTaskRequest { Id = named.Id });
        (await readNamed.Should().ThrowAsync<A2AException>()).Which.ErrorCode.Should().Be(A2AErrorCode.TaskNotFound);
        var readAnonymous = () => Sdk(specialist.Caller()).GetTaskAsync(new GetTaskRequest { Id = task.Id });
        (await readAnonymous.Should().ThrowAsync<A2AException>()).Which.ErrorCode.Should().Be(A2AErrorCode.TaskNotFound);
    }

    [Fact]
    public async Task AnonymousCallers_ShareOneBucketInTheActiveTaskCap()
    {
        var limiter = new ActiveTaskLimiter(maxTotal: 2, maxPerCaller: 1);

        limiter.TryAcquire("").Should().BeTrue();
        limiter.TryAcquire("").Should().BeFalse("the anonymous bucket is capped like any caller");
        limiter.TryAcquire("alice@example.com").Should().BeTrue();
        limiter.TryAcquire("bob@example.com").Should().BeFalse("and the per-process cap still applies");
        await Task.CompletedTask;
    }

    [Fact]
    public async Task TheLegacyRoutes_StillAnswerOnTheSameApp()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync();
        var http = specialist.Caller();

        var send = await http.PostAsync("/message:send", new StringContent("{\"message\":\"hi\"}", Encoding.UTF8, "application/json"));
        send.EnsureSuccessStatusCode();
        using var doc = JsonDocument.Parse(await send.Content.ReadAsStringAsync());
        doc.RootElement.GetProperty("response").GetString().Should().Be(ScriptedToolChatClient.Answer);

        var stream = await http.PostAsync("/message:stream", new StringContent("{\"message\":\"hi\"}", Encoding.UTF8, "application/json"));
        var body = await stream.Content.ReadAsStringAsync();
        body.Should().Contain("data: Hello ").And.Contain("data: [DONE]");

        (await http.GetAsync("/health")).EnsureSuccessStatusCode();
    }

    // ─────────────────────── bounds ───────────────────────

    [Fact]
    public async Task TheTaskStore_ForgetsItsOldestTasks()
    {
        var store = new OwnedBoundedTaskStore(maxTasks: 3);
        for (var i = 0; i < 5; i++)
        {
            await store.SaveTaskAsync($"t{i}", new AgentTask { Id = $"t{i}", ContextId = "c", Status = new global::A2A.TaskStatus { State = TaskState.Completed } });
        }

        store.Count.Should().Be(3);
        (await store.GetTaskAsync("t0")).Should().BeNull();
        (await store.GetTaskAsync("t1")).Should().BeNull();
        (await store.GetTaskAsync("t4")).Should().NotBeNull();
    }

    [Fact]
    public async Task ActiveTasks_AreCappedPerCaller_AndNothingQueues()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(
            mode: "hold", env: new Dictionary<string, string> { ["A2A_MAX_ACTIVE_TASKS_PER_CALLER"] = "1" });
        var alice = Sdk(specialist.Caller());

        // One streamed task is running (parked on the model); a second from the same user is refused outright.
        var first = StreamAsync(alice, Request());
        await WaitUntilAsync(() => specialist.Model.Started >= 1);

        var second = () => alice.SendMessageAsync(Request());
        var refused = (await second.Should().ThrowAsync<A2AException>()).Which;
        refused.Message.Should().Be(A2AWire.TooManyTasksText);
        refused.ErrorCode.Should().Be(A2AErrorCode.InternalError);

        // Another user is unaffected by alice's slot.
        var bobTask = Sdk(specialist.Caller(email: "bob@example.com")).SendMessageAsync(Request());
        await WaitUntilAsync(() => specialist.Model.Started >= 2);

        specialist.Model.Gate.TrySetResult();
        (await first).Last().StatusUpdate!.Status.State.Should().Be(TaskState.Completed);
        (await bobTask).Task!.Status.State.Should().Be(TaskState.Completed);

        // The slot is released: alice can run again.
        (await alice.SendMessageAsync(Request())).Task!.Status.State.Should().Be(TaskState.Completed);
    }

    [Fact]
    public async Task CancelTask_StopsARunningTask()
    {
        await using var specialist = await ScriptedSpecialist.StartAsync(mode: "hold");
        var client = Sdk(specialist.Caller());

        string? taskId = null;
        var events = new List<StreamResponse>();
        var running = Task.Run(async () =>
        {
            try
            {
                await foreach (var e in client.SendStreamingMessageAsync(Request()))
                {
                    events.Add(e);
                    taskId ??= e.Task?.Id;
                }
            }
            catch (Exception)
            {
                // the stream ends when the task is cancelled
            }
        });
        await WaitUntilAsync(() => taskId is not null && specialist.Model.Started >= 1);

        var canceled = await client.CancelTaskAsync(new CancelTaskRequest { Id = taskId! });

        canceled.Status.State.Should().Be(TaskState.Canceled);
        await running.WaitAsync(TimeSpan.FromSeconds(10));
        (await Sdk(specialist.Caller()).GetTaskAsync(new GetTaskRequest { Id = taskId! })).Status.State.Should().Be(TaskState.Canceled);
    }

    // ─────────────────────── errors outside the executor ───────────────────────

    [Fact]
    public async Task AFailingTaskStore_AnswersTheFixedInternalError_NotTheExceptionText()
    {
        // A signed-in caller: an anonymous one is refused before the store is ever touched (see
        // AnonymousCallers_CannotReadEachOthersStoredTasks), and this test is about the store failing.
        ECommerceAgents.Shared.Context.RequestContext.CurrentUserEmail = "alice@example.com";
        var logger = Microsoft.Extensions.Logging.Abstractions.NullLogger<A2AServer>.Instance;
        var server = new SpecialistA2AServer(
            new NoopHandler(), new ThrowingStore(), new ChannelEventNotifier(), logger, new A2AServerOptions { SupportsStreaming = true },
            new ActiveTaskLimiter(8, 8));

        var get = () => server.GetTaskAsync(new GetTaskRequest { Id = "x" }, null);
        var error = (await get.Should().ThrowAsync<A2AException>()).Which;

        error.Message.Should().Be(A2AWire.InternalErrorText);
        error.Message.Should().NotContain("secret_table");
        error.ErrorCode.Should().Be(A2AErrorCode.InternalError);

        var send = () => server.SendMessageAsync(Request(taskId: "x"), null);
        (await send.Should().ThrowAsync<A2AException>()).Which.Message.Should().Be(A2AWire.InternalErrorText);

        var stream = async () => { await foreach (var _ in server.SendStreamingMessageAsync(Request(taskId: "x"), null)) { } };
        (await stream.Should().ThrowAsync<A2AException>()).Which.Message.Should().Be(A2AWire.InternalErrorText);
    }

    // ─────────────────────── helpers ───────────────────────

    private static StringContent RpcBody(string method, object parameters) =>
        new(JsonSerializer.Serialize(new { jsonrpc = "2.0", id = "1", method, @params = parameters }, A2AJsonUtilities.DefaultOptions),
            Encoding.UTF8, "application/json");

    private static async Task WaitUntilAsync(Func<bool> condition)
    {
        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (!condition())
        {
            if (DateTime.UtcNow > deadline)
            {
                throw new TimeoutException("condition not met in 10s");
            }

            await Task.Delay(20);
        }
    }

    private sealed class NoopHandler : IAgentHandler
    {
        public Task ExecuteAsync(RequestContext context, AgentEventQueue eventQueue, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task CancelAsync(RequestContext context, AgentEventQueue eventQueue, CancellationToken cancellationToken) => Task.CompletedTask;
    }

    private sealed class ThrowingStore : ITaskStore
    {
        public Task<AgentTask?> GetTaskAsync(string taskId, CancellationToken cancellationToken = default) =>
            throw new InvalidOperationException("relation \"secret_table\" does not exist");

        public Task SaveTaskAsync(string taskId, AgentTask task, CancellationToken cancellationToken = default) =>
            throw new InvalidOperationException("relation \"secret_table\" does not exist");

        public Task DeleteTaskAsync(string taskId, CancellationToken cancellationToken = default) => Task.CompletedTask;

        public Task<ListTasksResponse> ListTasksAsync(ListTasksRequest request, CancellationToken cancellationToken = default) =>
            throw new InvalidOperationException("relation \"secret_table\" does not exist");
    }
}
