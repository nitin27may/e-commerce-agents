using System.Text.Json;
using ECommerceAgents.Shared.A2A.Protocol;
using ECommerceAgents.Shared.Context;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// The extension payloads as the other stack writes them. The Python SDK carries metadata as a protobuf
/// <c>Struct</c>, whose numbers are all doubles and so reach a .NET reader as <c>5.0</c>, <c>0.0</c>: a reader
/// that wants an integer must accept them, or a Python specialist's steps and usage silently vanish.
/// </summary>
public sealed class A2AWireTests
{
    private static JsonElement Json(string text) => JsonDocument.Parse(text).RootElement.Clone();

    [Fact]
    public void Steps_FromPython_ParseDespiteFloatNumbers()
    {
        var payload = Json(
            "{\"steps\":[{\"tool_name\":\"lookup\",\"tool_input\":{\"sku\":\"XM5\"},\"tool_output\":[\"79.99\"],"
            + "\"status\":\"success\",\"duration_ms\":12.0,\"agent\":\"product-discovery\",\"provenance\":{\"source\":\"tool:lookup\"}}],\"offset\":0.0}");

        var known = new List<A2AWire.WireStep>();
        var fresh = A2AWire.MergeSteps(known, payload);

        fresh.Should().ContainSingle();
        known[0].ToolName.Should().Be("lookup");
        known[0].Agent.Should().Be("product-discovery");
        known[0].DurationMs.Should().Be(12);
        known[0].ToExecutionStep().DurationMs.Should().Be(12);
    }

    [Fact]
    public void Usage_FromPython_ParsesDespiteFloatNumbers()
    {
        var usage = A2AWire.ParseUsage(Json(
            "{\"input_token_count\":5.0,\"output_token_count\":2.0,\"cached_input_token_count\":0.0,\"model\":\"gpt-4.1\"}"));

        usage.Should().Be(new SpecialistUsage(5, 2, 0, "gpt-4.1"));
    }

    [Theory]
    [InlineData("{}")]
    [InlineData("{\"input_token_count\":0,\"output_token_count\":0}")]
    [InlineData("[1,2]")]
    [InlineData("\"nope\"")]
    [InlineData("{\"input_token_count\":\"many\"}")]
    public void Usage_ThatIsEmptyOrMalformed_IsNull(string json) =>
        A2AWire.ParseUsage(Json(json)).Should().BeNull();

    [Fact]
    public void MergeSteps_IsIdempotentByIndex_SoTheTerminalRepeatAddsNothingNew()
    {
        var known = new List<A2AWire.WireStep>();
        var first = Json("{\"steps\":[{\"tool_name\":\"a\",\"status\":\"ok\",\"duration_ms\":1}],\"offset\":0}");
        var live = Json("{\"steps\":[{\"tool_name\":\"b\",\"status\":\"ok\",\"duration_ms\":1}],\"offset\":1}");
        var terminal = Json(
            "{\"steps\":[{\"tool_name\":\"a\",\"status\":\"ok\",\"duration_ms\":1},{\"tool_name\":\"b\",\"status\":\"ok\",\"duration_ms\":1}],\"offset\":0}");

        A2AWire.MergeSteps(known, first).Select(s => s.ToolName).Should().Equal("a");
        A2AWire.MergeSteps(known, live).Select(s => s.ToolName).Should().Equal("b");
        A2AWire.MergeSteps(known, terminal).Should().BeEmpty();
        known.Select(s => s.ToolName).Should().Equal("a", "b");
    }

    [Theory]
    [InlineData("\"not an object\"")]
    [InlineData("{\"steps\":\"x\"}")]
    [InlineData("{\"steps\":[1,2,3]}")]
    public void MergeSteps_IgnoresMalformedPayloads(string json)
    {
        var known = new List<A2AWire.WireStep>();

        A2AWire.MergeSteps(known, Json(json)).Should().BeEmpty();
        known.Should().BeEmpty();
    }
}
