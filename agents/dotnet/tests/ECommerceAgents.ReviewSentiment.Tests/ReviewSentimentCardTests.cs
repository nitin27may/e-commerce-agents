using ECommerceAgents.ReviewSentiment.Tools;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Tools;
using ECommerceAgents.TestFixtures;
using Xunit;

namespace ECommerceAgents.ReviewSentiment.Tests;

/// <summary>
/// The real review-sentiment specialist's public agent card, built from its real tool set (as <c>Program.cs</c> composes
/// it): no role-guarded tool is advertised and its customer tools are. Keep the tool list in step with <c>Program.cs</c>.
/// </summary>
public sealed class ReviewSentimentCardTests
{
    [Fact]
    public void TheAgentCard_ListsCustomerTools_AndNoRoleGuardedTool()
    {
        var pool = SpecialistCardCheck.OfflinePool();
        var settings = new AgentSettings();
        var tools = new ReviewTools(pool, settings).All()
            .Concat(new ProductLookupTools(pool).All())
            .Concat(new UserProfileTools(pool).All())
            .Concat(new MemoryTools(pool).All());

        SpecialistCardCheck.AssertCardHidesRestrictedTools("review-sentiment", tools, "draft_seller_response");
    }
}
