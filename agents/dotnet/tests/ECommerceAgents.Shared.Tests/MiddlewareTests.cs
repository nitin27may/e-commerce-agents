using ECommerceAgents.Shared.Middleware;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// Locks in parity with the Python middleware stack:
/// * ToolAuditMiddleware records tool invocation latency + error flag.
/// * PiiRedactor masks card- and SSN-shaped strings and counts hits.
/// * AgentRunLogger generates a short correlation id the caller can thread.
/// </summary>
public sealed class MiddlewareTests
{
    // ─────────────────────── ToolAuditMiddleware ─────────────

    [Fact]
    public async Task ToolAudit_RecordsLatencyAndErrorFlag()
    {
        var audit = new ToolAuditMiddleware(NullLogger<ToolAuditMiddleware>.Instance);
        var result = await audit.RecordAsync("tool.happy", async () =>
        {
            await Task.Delay(5);
            return 42;
        });
        result.Should().Be(42);

        audit.Audited.Should().HaveCount(1);
        audit.Audited.First().Tool.Should().Be("tool.happy");
        audit.Audited.First().Error.Should().BeNull();
        audit.Audited.First().ElapsedMs.Should().BeGreaterThanOrEqualTo(0);
    }

    [Fact]
    public async Task ToolAudit_RecordsExceptionBeforeRethrowing()
    {
        var audit = new ToolAuditMiddleware(NullLogger<ToolAuditMiddleware>.Instance);
        var act = async () => await audit.RecordAsync("tool.boom", () =>
            throw new InvalidOperationException("nope")
        );
        await act.Should().ThrowAsync<InvalidOperationException>();

        audit.Audited.Should().HaveCount(1);
        audit.Audited.First().Error.Should().StartWith("InvalidOperationException");
    }

    [Fact]
    public async Task ToolAudit_ConcurrentInvocationsAreThreadSafe()
    {
        var audit = new ToolAuditMiddleware(NullLogger<ToolAuditMiddleware>.Instance);
        var tasks = Enumerable.Range(0, 20).Select(i =>
            audit.RecordAsync($"tool.{i}", async () => { await Task.Yield(); return i; })
        );
        await Task.WhenAll(tasks);
        audit.Audited.Should().HaveCount(20);
    }

    // ─────────────────────── PiiRedactor ─────────────────────

    [Theory]
    [InlineData("pay with 4111 1111 1111 1111", "pay with [REDACTED-CARD]")]
    [InlineData("card 4111-1111-1111-1111 please", "card [REDACTED-CARD] please")]
    [InlineData("4111111111111111", "[REDACTED-CARD]")]
    public void PiiRedactor_MasksCardNumbers(string input, string expected)
    {
        var redactor = new PiiRedactor();
        redactor.Redact(input).Should().Be(expected);
        redactor.Redactions.Should().Be(1);
    }

    [Fact]
    public void PiiRedactor_MasksSsn()
    {
        var redactor = new PiiRedactor();
        redactor.Redact("SSN 123-45-6789 here").Should().Be("SSN [REDACTED-SSN] here");
        redactor.Redactions.Should().Be(1);
    }

    [Fact]
    public void PiiRedactor_AccumulatesCount()
    {
        var redactor = new PiiRedactor();
        redactor.Redact("4111 1111 1111 1111 and 123-45-6789 and 4222 2222 2222 2222");
        redactor.Redactions.Should().Be(3);
    }

    [Fact]
    public void PiiRedactor_LeavesSafeTextAlone()
    {
        var redactor = new PiiRedactor();
        redactor.Redact("Hello world — order 12345").Should().Be("Hello world — order 12345");
        redactor.Redactions.Should().Be(0);
    }

    [Fact]
    public void PiiRedactor_HandlesNullAndEmpty()
    {
        var redactor = new PiiRedactor();
        redactor.Redact(null).Should().BeEmpty();
        redactor.Redact("").Should().BeEmpty();
    }

    // ─────────────────────── AgentRunLogger ──────────────────

    [Fact]
    public async Task AgentRunLogger_PassesResultThroughAndGeneratesRunId()
    {
        var runner = new AgentRunLogger(NullLogger<AgentRunLogger>.Instance);
        string? captured = null;
        var result = await runner.RunAsync("orchestrator", async runId =>
        {
            captured = runId;
            await Task.Yield();
            return "ok";
        });
        result.Should().Be("ok");
        captured.Should().NotBeNullOrEmpty();
        captured!.Length.Should().Be(8);
    }

    [Fact]
    public async Task AgentRunLogger_ReThrowsExceptions()
    {
        var runner = new AgentRunLogger(NullLogger<AgentRunLogger>.Instance);
        var act = async () =>
            await runner.RunAsync<int>("orchestrator", _ =>
                throw new InvalidOperationException("down")
            );
        await act.Should().ThrowAsync<InvalidOperationException>();
    }
}
