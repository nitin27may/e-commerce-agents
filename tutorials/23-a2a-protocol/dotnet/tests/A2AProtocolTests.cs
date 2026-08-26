// MAF v1 — Chapter 23 tests (A2A Protocol)
//
// A2A is a protocol, so the tests are wire-shape tests: does the agent card
// carry what a caller needs, does /message:send answer in the documented
// envelope, does the SSE stream frame its data and terminate the way callers
// expect. None of that is guaranteed by anything a compiler can see.
//
// The SSE assertions matter most, because streaming is where the failure modes
// hide. A stream that never sends [DONE] leaves the caller hanging on a
// connection that is technically fine, and a stream that reports failure with
// an HTTP status cannot — the status line was sent before anything went wrong.
// Both look like a network problem from the outside.

using System.Net;
using System.Net.Http.Json;
using FluentAssertions;
using MafV1.Shared.Testing;
using Xunit;

namespace MafV1.Ch23.A2AProtocol.Tests;

public sealed class A2AProtocolTests
{
    /// <summary>Calls the specialist once, then reports what came back.</summary>
    private static ScriptedChatClient Coordinating(string question) => new(call =>
        call.Text.Contains("result:")
            ? ScriptedChatClient.Text("Your order has shipped.")
            : ScriptedChatClient.ToolCall("call_order_specialist",
                new Dictionary<string, object?> { ["message"] = question }));

    // ─────────────── The pure lookup ───────────────

    [Theory]
    [InlineData("What's the status of ORD-1001?", "Shipped, arriving 2026-08-22.")]
    [InlineData("ord-1002 please", "Processing — not yet shipped.")]
    [InlineData("Any news on ORD-1003?", "Delivered on 2026-08-15.")]
    public void A_Known_Order_Id_Is_Found_Anywhere_In_The_Message(string message, string expected)
    {
        // Case-insensitive, and extracted from prose rather than requiring a
        // bare id — the model forwards the question verbatim, so the specialist
        // has to cope with a sentence.
        Program.LookupOrder(message).Should().Be(expected);
    }

    [Fact]
    public void An_Unknown_Order_Id_Is_Reported_As_Not_Found()
    {
        Program.LookupOrder("Where is ORD-9999?").Should().Contain("No order found with id ORD-9999");
    }

    [Fact]
    public void A_Message_With_No_Order_Id_Says_What_It_Expected()
    {
        // Naming the expected format is what turns a dead end into a retry.
        Program.LookupOrder("where's my stuff").Should().Contain("ORD-1001");
    }

    // ─────────────── GET /.well-known/agent-card.json ───────────────

    [Fact]
    public async Task The_Agent_Card_Carries_Identity_And_Version()
    {
        AgentCard? card = await Program.FetchAgentCardAsync();

        card.Should().NotBeNull();
        card!.Name.Should().Be("order-lookup");
        card.Version.Should().NotBeNullOrWhiteSpace();
        card.Description.Should().NotBeNullOrWhiteSpace();
        card.Url.Should().Be(Program.SpecialistBaseUrl);
    }

    [Fact]
    public async Task The_Agent_Card_Is_Served_At_The_Well_Known_Path()
    {
        // The path is the discoverable part of the protocol. Serving the same
        // document somewhere else means no caller can find it without being
        // told, which defeats the point.
        HttpClient client = await Program.SpecialistClientAsync();

        HttpResponseMessage response = await client.GetAsync("/.well-known/agent-card.json");

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        response.Content.Headers.ContentType!.MediaType.Should().Be("application/json");
    }

    // ─────────────── POST /message:send ───────────────

    [Fact]
    public async Task Message_Send_Returns_The_Answer_In_A_Response_Field()
    {
        HttpClient client = await Program.SpecialistClientAsync();

        HttpResponseMessage response = await client.PostAsJsonAsync(
            "/message:send", new A2ARequest("What's the status of ORD-1001?"));

        response.StatusCode.Should().Be(HttpStatusCode.OK);

        A2AResponse? body = await response.Content.ReadFromJsonAsync<A2AResponse>();
        body!.Response.Should().Be("Shipped, arriving 2026-08-22.");
        body.Steps.Should().NotBeNull("callers iterate steps unconditionally");
    }

    [Fact]
    public async Task Message_Send_Rejects_An_Empty_Message_With_400()
    {
        // A blocking call CAN fail with a status code, because nothing has been
        // sent yet. Contrast with the streaming endpoint below — the asymmetry
        // is the interesting part.
        HttpClient client = await Program.SpecialistClientAsync();

        HttpResponseMessage response = await client.PostAsJsonAsync("/message:send", new A2ARequest(""));

        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    // ─────────────── POST /message:stream ───────────────

    [Fact]
    public async Task The_Stream_Delivers_The_Answer_As_Data_Frames()
    {
        IReadOnlyList<string> chunks = await Program.StreamCallAsync("What's the status of ORD-1001?");

        chunks.Should().ContainSingle().Which.Should().Be("Shipped, arriving 2026-08-22.");
    }

    [Fact]
    public async Task The_Done_Sentinel_Is_Consumed_Rather_Than_Returned_As_Content()
    {
        // A caller that forgets to strip [DONE] renders it to the user. It has
        // happened in this repo's own history, which is why it is asserted.
        IReadOnlyList<string> chunks = await Program.StreamCallAsync("ORD-1002");

        chunks.Should().NotContain("[DONE]");
    }

    [Fact]
    public async Task The_Stream_Is_Content_Type_Event_Stream()
    {
        // Without this header a proxy will happily buffer the whole response
        // and hand it over at the end — the stream still "works" and stops
        // being a stream.
        HttpClient client = await Program.SpecialistClientAsync();

        using var request = new HttpRequestMessage(HttpMethod.Post, "/message:stream")
        {
            Content = JsonContent.Create(new A2ARequest("ORD-1001")),
        };

        using HttpResponseMessage response = await client.SendAsync(
            request, HttpCompletionOption.ResponseHeadersRead);

        response.Content.Headers.ContentType!.MediaType.Should().Be("text/event-stream");
    }

    [Fact]
    public async Task A_Stream_Failure_Arrives_As_A_Frame_Not_A_Status_Code()
    {
        // The asymmetry with /message:send. By the time a stream fails, 200 OK
        // has already been sent — so the failure has to travel in-band, and a
        // caller that only checks the status code sees a successful empty
        // answer.
        var act = async () => await Program.StreamCallAsync("   ");

        (await act.Should().ThrowAsync<InvalidOperationException>())
            .Which.Message.Should().Contain("[ERROR");
    }

    [Fact]
    public async Task Both_Transports_Agree_On_The_Answer()
    {
        // Same question, two endpoints, one answer. If they ever diverge, the
        // streaming path has grown its own copy of the logic.
        HttpClient client = await Program.SpecialistClientAsync();
        HttpResponseMessage blocking = await client.PostAsJsonAsync(
            "/message:send", new A2ARequest("ORD-1003"));

        A2AResponse? body = await blocking.Content.ReadFromJsonAsync<A2AResponse>();
        IReadOnlyList<string> streamed = await Program.StreamCallAsync("ORD-1003");

        string.Join(string.Empty, streamed).Should().Be(body!.Response);
    }

    // ─────────────── The coordinator ───────────────

    [Fact]
    public async Task The_Coordinator_Reaches_The_Specialist_Over_Http()
    {
        // End to end across the boundary: a scripted model, a real tool, a real
        // HTTP round trip, and a real answer coming back into the model's
        // context.
        ScriptedChatClient fake = Coordinating("What's the status of ORD-1001?");

        await Program.AskAsync(Program.BuildAgent(fake), Program.DefaultQuestion);

        fake.Calls.Should().HaveCount(2);
        fake.Calls[1].Text.Should().Contain("Shipped, arriving 2026-08-22.");
    }

    [Fact]
    public async Task The_Tool_Returns_Only_The_Response_Field_Not_The_Envelope()
    {
        // The model should see an answer, not JSON. Leaking the envelope is how
        // an agent starts quoting {"response": ...} at customers.
        string result = await Program.CallOrderSpecialist("ORD-1001");

        result.Should().Be("Shipped, arriving 2026-08-22.");
        result.Should().NotContain("{").And.NotContain("steps");
    }

    [Fact]
    public async Task An_Unknown_Order_Still_Comes_Back_As_A_Normal_Answer()
    {
        // Not an exception. A specialist that throws for a plausible-but-wrong
        // id turns a routine "we can't find that" into a failed turn.
        string result = await Program.CallOrderSpecialist("ORD-9999");

        result.Should().Contain("No order found");
    }

    [Fact]
    public void The_Coordinator_Is_Told_To_Forward_The_Question_Verbatim()
    {
        // The specialist parses the order id itself, so paraphrasing loses it.
        Program.Instructions.Should().Contain("verbatim");
    }
}
