using ECommerceAgents.PricingPromotions.Tools;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Tools;
using ECommerceAgents.TestFixtures;
using Xunit;

namespace ECommerceAgents.PricingPromotions.Tests;

/// <summary>
/// The real pricing-promotions specialist's public agent card, built from its real tool set (as <c>Program.cs</c> composes
/// it): no role-guarded tool is advertised and its customer tools are. Keep the tool list in step with <c>Program.cs</c>.
/// </summary>
public sealed class PricingPromotionsCardTests
{
    [Fact]
    public void TheAgentCard_ListsCustomerTools_AndNoRoleGuardedTool()
    {
        var pool = SpecialistCardCheck.OfflinePool();
        var settings = new AgentSettings();
        var tools = new PricingTools(pool).All()
            .Concat(new ProductLookupTools(pool).All())
            .Concat(new UserProfileTools(pool).All())
            .Concat(new PriceHistoryTools(pool).All())
            .Concat(new LoyaltyTools(pool).All());

        SpecialistCardCheck.AssertCardHidesRestrictedTools("pricing-promotions", tools);
    }
}
