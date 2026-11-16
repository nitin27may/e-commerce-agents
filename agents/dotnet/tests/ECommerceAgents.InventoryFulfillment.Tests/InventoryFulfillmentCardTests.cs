using ECommerceAgents.InventoryFulfillment.Tools;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Tools;
using ECommerceAgents.TestFixtures;
using Xunit;

namespace ECommerceAgents.InventoryFulfillment.Tests;

/// <summary>
/// The real inventory-fulfillment specialist's public agent card, built from its real tool set (as <c>Program.cs</c> composes
/// it): no role-guarded tool is advertised and its customer tools are. Keep the tool list in step with <c>Program.cs</c>.
/// </summary>
public sealed class InventoryFulfillmentCardTests
{
    [Fact]
    public void TheAgentCard_ListsCustomerTools_AndNoRoleGuardedTool()
    {
        var pool = SpecialistCardCheck.OfflinePool();
        var settings = new AgentSettings();
        var tools = new InventoryTools(pool, settings).All()
            .Concat(new ProductLookupTools(pool).All())
            .Concat(new UserProfileTools(pool).All())
            .Concat(new StockLookupTools(pool).All());

        SpecialistCardCheck.AssertCardHidesRestrictedTools("inventory-fulfillment", tools, "calculate_fulfillment_plan", "place_backorder");
    }
}
