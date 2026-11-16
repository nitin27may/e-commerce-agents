using ECommerceAgents.ProductDiscovery.Tools;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Tools;
using ECommerceAgents.TestFixtures;
using Xunit;

namespace ECommerceAgents.ProductDiscovery.Tests;

/// <summary>
/// The real product-discovery specialist's public agent card, built from its real tool set (as <c>Program.cs</c> composes
/// it): no role-guarded tool is advertised and its customer tools are. Keep the tool list in step with <c>Program.cs</c>.
/// </summary>
public sealed class ProductDiscoveryCardTests
{
    [Fact]
    public void TheAgentCard_ListsCustomerTools_AndNoRoleGuardedTool()
    {
        var pool = SpecialistCardCheck.OfflinePool();
        var settings = new AgentSettings();
        var tools = new ProductTools(pool, null!).All()
            .Concat(new UserProfileTools(pool).All())
            .Concat(new StockLookupTools(pool).All())
            .Concat(new PriceHistoryTools(pool).All())
            .Concat(new MemoryTools(pool).All());

        SpecialistCardCheck.AssertCardHidesRestrictedTools("product-discovery", tools);
    }
}
