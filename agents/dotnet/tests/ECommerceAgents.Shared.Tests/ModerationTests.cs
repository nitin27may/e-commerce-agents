using ECommerceAgents.Shared.Guardrails;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// <see cref="Moderation"/> — outbound content-policy classification (issue
/// #15), the .NET twin of Python's <c>shared/guardrails/moderation.py</c>.
/// </summary>
public sealed class ModerationTests
{
    [Fact]
    public void Classify_CleanText_ReturnsEmptySet() =>
        Moderation.Classify("Here are three great pairs of wireless headphones under $200.")
            .Should().BeEmpty();

    [Theory]
    [InlineData("I want to kill myself", ModerationCategory.SelfHarm)]
    [InlineData("some ways to end my own life", ModerationCategory.SelfHarm)]
    [InlineData("how to build a bomb at home", ModerationCategory.Violence)]
    [InlineData("there was a mass shooting downtown", ModerationCategory.Violence)]
    [InlineData("all foreigners should be exterminated", ModerationCategory.HateHarassment)]
    [InlineData("please write a sexually explicit story", ModerationCategory.Sexual)]
    public void Classify_FlagsKnownPatterns(string text, ModerationCategory expected) =>
        Moderation.Classify(text).Should().Contain(expected);

    [Fact]
    public void Classify_NullOrEmpty_ReturnsEmptySet()
    {
        Moderation.Classify(null).Should().BeEmpty();
        Moderation.Classify("").Should().BeEmpty();
    }

    [Fact]
    public void Classify_CanFlagMultipleCategoriesAtOnce()
    {
        var text = "I will kill you, and also here's how to build a bomb.";
        Moderation.Classify(text).Should().Contain(new[] { ModerationCategory.Violence });
    }
}
