using ECommerceAgents.OrderManagement.Tools;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Tools;
using ECommerceAgents.TestFixtures;
using Xunit;

namespace ECommerceAgents.OrderManagement.Tests;

/// <summary>
/// The real order-management specialist's public agent card, built from its real tool set (as <c>Program.cs</c> composes
/// it): no role-guarded tool is advertised and its customer tools are. Every guarded order tool (cancel_order,
/// modify_order, initiate_return, process_refund) is <c>[RequiresRole("customer", "seller")]</c>, so a customer may
/// call all of them and none is restricted; the card must list them. Keep the tool list in step with <c>Program.cs</c>.
/// </summary>
public sealed class OrderManagementCardTests
{
    [Fact]
    public void TheAgentCard_ListsCustomerTools_AndNoRoleGuardedTool()
    {
        var pool = SpecialistCardCheck.OfflinePool();
        var settings = new AgentSettings();
        var tools = new OrderTools(pool, settings).All()
            .Concat(new UserProfileTools(pool).All())
            .Concat(new ReturnTools(pool, settings).All());

        SpecialistCardCheck.AssertCardHidesRestrictedTools("order-management", tools);
    }
}
