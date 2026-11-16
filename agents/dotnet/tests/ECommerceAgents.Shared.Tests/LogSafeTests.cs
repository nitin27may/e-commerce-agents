using ECommerceAgents.Shared.Logging;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>The twin of Python's <c>tests/test_log_safe.py</c>: line breaks never reach a log line.</summary>
public sealed class LogSafeTests
{
    [Theory]
    [InlineData("plain", "plain")]
    [InlineData("a\nb", "ab")]
    [InlineData("a\r\nb", "ab")]
    [InlineData("id=1\nINFO forged line\r", "id=1INFO forged line")]
    [InlineData(null, "")]
    public void LineBreaksAreRemoved(string? raw, string expected) => LogSafe.Clean(raw).Should().Be(expected);

    [Fact]
    public void NonStringValuesAreStringified()
    {
        var id = Guid.NewGuid();
        LogSafe.Clean(id).Should().Be(id.ToString());
        LogSafe.Clean(42).Should().Be("42");
    }

    [Fact]
    public void LengthIsCapped()
    {
        var cleaned = LogSafe.Clean(new string('x', 1000));
        cleaned.Should().Be(new string('x', LogSafe.MaxLength) + "...");
        LogSafe.Clean("xxxxxxxxxx", 4).Should().Be("xxxx...");
    }
}
