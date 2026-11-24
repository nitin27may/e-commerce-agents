using System.Text.Json;
using ECommerceAgents.Shared.Approvals;
using ECommerceAgents.Shared.Middleware;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// The customer confirmation vector file Python runs too (<c>tests/test_customer_confirmation.py</c>):
/// <c>agents/python/config/policies/testdata/customer-confirmation.v1.json</c>. It pins what counts as an explicit yes or a clear no,
/// which arguments make two requests the same action, the key itself and the sentences the customer reads, so the two stacks
/// cannot drift on any of them.
/// </summary>
public sealed class CustomerConfirmationVectorTests
{
    private static readonly JsonElement Vectors = Load();

    private static JsonElement Load()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "agents", "python", "config")))
        {
            dir = dir.Parent;
        }

        dir.Should().NotBeNull();
        var path = Path.Combine(dir!.FullName, "agents", "python", "config", "policies", "testdata", "customer-confirmation.v1.json");
        return JsonDocument.Parse(File.ReadAllText(path)).RootElement.Clone();
    }

    private static TheoryData<string> Strings(string section)
    {
        var data = new TheoryData<string>();
        foreach (var item in Vectors.GetProperty(section).EnumerateArray())
        {
            data.Add(item.GetString()!);
        }

        return data;
    }

    public static TheoryData<string> Affirmative() => Strings("affirmative");
    public static TheoryData<string> NotAffirmative() => Strings("not_affirmative");
    public static TheoryData<string> Decline() => Strings("decline");
    public static TheoryData<string> NotDecline() => Strings("not_decline");

    [Theory]
    [MemberData(nameof(Affirmative))]
    public void ExplicitYes_IsAffirmative(string message) => CustomerConfirmation.IsAffirmative(message).Should().BeTrue();

    [Theory]
    [MemberData(nameof(NotAffirmative))]
    public void AnythingElse_IsNotAYes(string message) => CustomerConfirmation.IsAffirmative(message).Should().BeFalse();

    [Theory]
    [MemberData(nameof(Decline))]
    public void ClearNo_IsADecline(string message) => CustomerConfirmation.IsDecline(message).Should().BeTrue();

    [Theory]
    [MemberData(nameof(NotDecline))]
    public void SilenceAboutTheQuestion_IsNotADecline(string message) => CustomerConfirmation.IsDecline(message).Should().BeFalse();

    [Fact]
    public void Null_IsNeither()
    {
        CustomerConfirmation.IsAffirmative(null).Should().BeFalse();
        CustomerConfirmation.IsDecline(null).Should().BeFalse();
    }

    [Fact]
    public void Sentences_MatchTheSharedVector()
    {
        CustomerConfirmation.NotSubmitted.Should().Be(Vectors.GetProperty("not_submitted").GetString());
        CustomerConfirmation.DeclinedReply.Should().Be(Vectors.GetProperty("declined_reply").GetString());
        foreach (var c in Vectors.GetProperty("prompts").EnumerateArray())
        {
            CustomerConfirmation.Prompt(c.GetProperty("tool").GetString()!, c.GetProperty("input"))
                .Should().Be(c.GetProperty("sentence").GetString());
        }
    }

    private static string Key(JsonElement c) => CustomerConfirmation.ArgsKey(c.GetProperty("tool").GetString()!, c.GetProperty("input"));

    [Fact]
    public void TheKey_IgnoresWordingAndCase_ButNotTheAction()
    {
        foreach (var group in Vectors.GetProperty("same_action").EnumerateArray())
        {
            group.EnumerateArray().Select(Key).Distinct().Should().HaveCount(1);
        }

        foreach (var group in Vectors.GetProperty("different_action").EnumerateArray())
        {
            group.EnumerateArray().Select(Key).Distinct().Should().HaveCount(2);
        }
    }

    [Fact]
    public void Keys_MatchTheSharedVector()
    {
        foreach (var c in Vectors.GetProperty("keys").EnumerateArray())
        {
            var tool = c.GetProperty("tool").GetString()!;
            var expected = new SortedDictionary<string, object>(StringComparer.Ordinal);
            foreach (var property in c.GetProperty("normalized").EnumerateObject())
            {
                expected[property.Name] = property.Value.GetString()!;
            }

            CustomerConfirmation.Canonical(CustomerConfirmation.NormalizedArguments(tool, c.GetProperty("input")))
                .Should().Be(CustomerConfirmation.Canonical(expected));
            Key(c).Should().Be(c.GetProperty("key").GetString());
        }
    }

    [Fact]
    public void EveryGatedTool_HasADescriptionAndKeyFields()
    {
        CustomerConfirmation.KnownTools.Should().BeEquivalentTo(HitlGate.GatedTools.Select(ApprovalIntake.CanonicalToolName));
        using var empty = JsonDocument.Parse("{}");
        foreach (var tool in CustomerConfirmation.KnownTools)
        {
            CustomerConfirmation.DescribeAction(tool, empty.RootElement).Should().NotBeEmpty().And.NotContain("None");
        }
    }
}
