// MAF v1 — Chapter 27 tests (Agent-as-tool)
//
// The chapter's claim is that wrapping an agent as a tool composes rather than
// delegates: the coordinator stays in charge, gets a string back, and can then
// call other tools and combine the results. Everything below is an attempt to
// prove that from the outside — by watching what actually reaches the model —
// rather than taking the API's word for it.
//
// The scripted client answers by role, because the coordinator and the
// specialist have different instructions and different toolsets. That
// difference is itself the most useful assertion in the file: if the wrap
// leaked, both agents would see the same tools.

using FluentAssertions;
using MafV1.Shared.Testing;
using Microsoft.Agents.AI;
using Xunit;

namespace MafV1.Ch27.AgentAsTool.Tests;

public sealed class AgentAsToolTests
{
    private static bool IsCoordinator(ScriptedCall call) => call.Instructions.Contains("coordinator");
    private static bool IsSpecialist(ScriptedCall call) => call.Instructions.Contains("product-lookup specialist");

    /// <summary>
    /// Drives the full two-agent, three-call sequence: coordinator delegates,
    /// specialist looks up, coordinator discounts, coordinator answers.
    /// </summary>
    private static ScriptedChatClient FullConversation() => new(call =>
    {
        if (IsSpecialist(call))
        {
            return call.Text.Contains("result:")
                ? ScriptedChatClient.Text("Wireless Headphones: $149.99, category Electronics, 42 in stock.")
                : ScriptedChatClient.ToolCall("search_catalog",
                    new Dictionary<string, object?> { ["name"] = "wireless headphones" });
        }

        // Coordinator. Drive it forward by what it has already seen.
        if (call.Text.Contains("119.99"))
        {
            return ScriptedChatClient.Text("The Wireless Headphones are $149.99, or $119.99 after 20% off.");
        }

        if (call.Text.Contains("149.99"))
        {
            return ScriptedChatClient.ToolCall("calculate_discount",
                new Dictionary<string, object?> { ["price"] = 149.99, ["percent"] = 20.0 });
        }

        return ScriptedChatClient.ToolCall("product_lookup",
            new Dictionary<string, object?> { ["query"] = "Wireless Headphones price" });
    });

    // ─────────────── The catalogue tool ───────────────

    [Fact]
    public void SearchCatalog_Reports_Price_Category_And_Stock()
    {
        Program.SearchCatalog("Wireless Headphones").Should()
            .Contain("$149.99").And.Contain("Electronics").And.Contain("42 in stock");
    }

    [Fact]
    public void SearchCatalog_Is_Case_And_Whitespace_Insensitive()
    {
        // The model will not reliably echo the exact casing from the catalogue.
        // A lookup that only matches "wireless headphones" exactly fails on the
        // first natural-sounding question.
        Program.SearchCatalog("  WIRELESS HEADPHONES  ").Should().Contain("$149.99");
    }

    [Fact]
    public void SearchCatalog_Says_So_For_An_Unknown_Product()
    {
        // Not an exception. A tool that throws on a miss turns a normal "we
        // don't stock that" into an agent error the user sees as a crash.
        Program.SearchCatalog("submarine").Should().Be("No catalog entry for 'submarine'.");
    }

    [Fact]
    public void SearchCatalog_Reports_Out_Of_Stock_Rather_Than_Hiding_The_Item()
    {
        Program.SearchCatalog("coffee maker").Should().Contain("0 in stock");
    }

    [Theory]
    [InlineData(100.0, 20.0, "$80.00")]
    [InlineData(149.99, 20.0, "$119.99")]
    [InlineData(50.0, 0.0, "$50.00")]
    [InlineData(50.0, 100.0, "$0.00")]
    public void CalculateDiscount_Computes_The_Discounted_Price(double price, double percent, string expected)
    {
        Program.CalculateDiscount(price, percent).Should().StartWith(expected);
    }

    [Fact]
    public void CalculateDiscount_Shows_Its_Working()
    {
        // The coordinator quotes this string back to the user, so it has to
        // stand on its own — "$119.99" alone is not an answer to "what's the
        // price after 20% off?"
        Program.CalculateDiscount(149.99, 20).Should().Be("$119.99 (after 20% off $149.99)");
    }

    // ─────────────── The wrap ───────────────

    [Fact]
    public async Task The_Coordinator_Is_Offered_The_Wrapped_Agent_As_An_Ordinary_Tool()
    {
        ScriptedChatClient fake = FullConversation();

        await Program.AskAsync(Program.BuildAgent(fake), Program.DefaultQuestion);

        ScriptedCall first = fake.Calls.First(IsCoordinator);
        first.Tools.Should().BeEquivalentTo(new[] { "product_lookup", "calculate_discount" });
    }

    [Fact]
    public async Task The_Wrapped_Agents_Own_Tools_Are_Not_Exposed_To_The_Coordinator()
    {
        // The encapsulation claim. If search_catalog leaked upward, the
        // coordinator could bypass the specialist entirely — and would,
        // eventually, on some prompt nobody tested.
        ScriptedChatClient fake = FullConversation();

        await Program.AskAsync(Program.BuildAgent(fake), Program.DefaultQuestion);

        fake.Calls.Where(IsCoordinator).Should().OnlyContain(c => !c.Tools.Contains("search_catalog"));
    }

    [Fact]
    public async Task The_Specialist_Gets_Its_Own_Instructions_And_Its_Own_Tool()
    {
        ScriptedChatClient fake = FullConversation();

        await Program.AskAsync(Program.BuildAgent(fake), Program.DefaultQuestion);

        ScriptedCall specialist = fake.Calls.First(IsSpecialist);
        specialist.Instructions.Should().Be(Program.ProductLookupInstructions);
        specialist.Tools.Should().ContainSingle().Which.Should().Be("search_catalog");
    }

    [Fact]
    public async Task The_Specialist_Really_Is_Invoked()
    {
        // Without this, every assertion above could pass against a tool that
        // was declared and never called.
        ScriptedChatClient fake = FullConversation();

        await Program.AskAsync(Program.BuildAgent(fake), Program.DefaultQuestion);

        fake.Calls.Should().Contain(c => IsSpecialist(c));
    }

    [Fact]
    public async Task Control_Returns_To_The_Coordinator_After_The_Specialist_Answers()
    {
        // The difference from a handoff, stated as an assertion: the last word
        // belongs to the coordinator, not the specialist.
        ScriptedChatClient fake = FullConversation();

        string answer = await Program.AskAsync(Program.BuildAgent(fake), Program.DefaultQuestion);

        IsCoordinator(fake.Calls[^1]).Should().BeTrue();
        answer.Should().Contain("$119.99");
    }

    [Fact]
    public async Task The_Coordinator_Composes_The_Specialists_Answer_With_A_Local_Tool()
    {
        // The composition claim end to end: delegate, then discount, then
        // answer — three coordinator turns around one specialist turn.
        ScriptedChatClient fake = FullConversation();

        string answer = await Program.AskAsync(Program.BuildAgent(fake), Program.DefaultQuestion);

        answer.Should().Contain("$149.99").And.Contain("$119.99");
        fake.Calls.Count(IsCoordinator).Should().Be(3);
        fake.Calls.Count(IsSpecialist).Should().Be(2);
    }

    [Fact]
    public async Task The_Specialist_Receives_The_Task_The_Coordinator_Sent_It()
    {
        // The wrapped agent takes a string argument like any tool. If the
        // coordinator's task description did not reach it, the specialist
        // would be answering a question nobody asked.
        ScriptedChatClient fake = FullConversation();

        await Program.AskAsync(Program.BuildAgent(fake), Program.DefaultQuestion);

        fake.Calls.First(IsSpecialist).Text.Should().Contain("Wireless Headphones");
    }

    [Fact]
    public void The_Two_Agents_Have_Different_Instructions()
    {
        Program.CoordinatorInstructions.Should().NotBe(Program.ProductLookupInstructions);
    }

    [Fact]
    public void The_Specialist_Agent_Builds_Standalone()
    {
        // It is a perfectly ordinary agent — being wrapped is something done
        // TO it, not something it has to know about.
        AIAgent specialist = Program.BuildProductLookupAgent(new ScriptedChatClient("hi"));

        specialist.Name.Should().Be("product-lookup-agent");
    }
}
