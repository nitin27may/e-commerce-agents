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

    [Theory]
    [InlineData("{\"tool_name\":\"lookup\",\"tool_input\":{\"sku\":\"XM5\"},\"tool_output\":\"ok\",\"status\":\"success\",\"duration_ms\":12.0,\"agent\":\"a\"}")]
    [InlineData("{\"toolName\":\"lookup\",\"toolInput\":{\"sku\":\"XM5\"},\"toolOutput\":\"ok\",\"status\":\"success\",\"durationMs\":12,\"agent\":\"a\"}")]
    [InlineData("{\"ToolName\":\"lookup\",\"ToolInput\":{\"sku\":\"XM5\"},\"ToolOutput\":\"ok\",\"Status\":\"success\",\"DurationMs\":12,\"Agent\":\"a\"}")]
    public void LegacySteps_AreReadWhicheverStackWroteThem(string json)
    {
        var options = new JsonSerializerOptions(JsonSerializerDefaults.Web) { Converters = { new TolerantStepConverter() } };

        var step = JsonSerializer.Deserialize<ExecutionStep>(json, options)!;

        step.ToolName.Should().Be("lookup");
        step.Status.Should().Be("success");
        step.DurationMs.Should().Be(12);
        step.Agent.Should().Be("a");
        step.ToolInput!.ToString().Should().Contain("XM5");
        step.ToolOutput!.ToString().Should().Be("ok");
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

    [Theory]
    [InlineData("{\"input_token_count\":-500,\"output_token_count\":10}", 0, 10)]
    [InlineData("{\"input_token_count\":7,\"output_token_count\":-3,\"cached_input_token_count\":-9}", 7, 0)]
    public void Usage_WithANegativeCount_TreatsItAsAbsent(string json, long input, long output)
    {
        var usage = A2AWire.ParseUsage(Json(json));

        usage.Should().NotBeNull();
        (usage!.InputTokenCount, usage.OutputTokenCount, usage.CachedInputTokenCount).Should().Be((input, output, 0L));
    }

    [Fact]
    public void Usage_ThatIsOnlyNegative_IsNull() =>
        A2AWire.ParseUsage(Json("{\"input_token_count\":-500,\"output_token_count\":-10}")).Should().BeNull();

    [Fact]
    public void RunUsage_NeverRecordsANegativeCount_WhateverWireItCameOver()
    {
        var usage = new RunUsage();
        usage.Add("a", new SpecialistUsage(100, 20, 0, "m"));

        usage.Add("a", new SpecialistUsage(-500, 5, -1, "m"));
        usage.Add("b", new SpecialistUsage(-1, -1, 0, "m"));

        usage.ByAgent["a"].Should().Be(new SpecialistUsage(100, 25, 0, "m"));
        usage.ByAgent.Should().NotContainKey("b");
        usage.Total()!.InputTokenCount.Should().Be(100);
    }

    [Theory]
    [InlineData("-1")]
    [InlineData("2147483648")]
    [InlineData("1099511627776")]
    [InlineData("1e30")]
    [InlineData("0.5")]
    [InlineData("\"0\"")]
    [InlineData("true")]
    public void MergeSteps_WithAnOffsetOutsideZeroToIntMax_IgnoresTheBatch(string offset)
    {
        var known = new List<A2AWire.WireStep>();

        var fresh = A2AWire.MergeSteps(known, Json("{\"steps\":[{\"tool_name\":\"a\",\"status\":\"ok\",\"duration_ms\":1}],\"offset\":" + offset + "}"));

        fresh.Should().BeEmpty();
        known.Should().BeEmpty();
    }

    [Theory]
    [InlineData("0")]
    [InlineData("0.0")]
    public void MergeSteps_AcceptsAWholeOffset_IntOrStructFloat(string offset)
    {
        var known = new List<A2AWire.WireStep>();

        A2AWire.MergeSteps(known, Json("{\"steps\":[{\"tool_name\":\"a\",\"status\":\"ok\",\"duration_ms\":1}],\"offset\":" + offset + "}"))
            .Should().ContainSingle();
    }

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
