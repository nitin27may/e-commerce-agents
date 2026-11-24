using ECommerceAgents.InventoryFulfillment.Tools;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Middleware;
using FluentAssertions;
using Microsoft.Extensions.AI;
using Xunit;

namespace ECommerceAgents.InventoryFulfillment.Tests;

/// <summary>
/// The gate's role layer reads <c>[RequiresRole]</c> off the registered tool function (<see cref="HitlGate.RolesOf"/>). Pins that
/// <c>place_backorder</c> exposes seller and admin, so a customer is refused before an approval is opened.
/// </summary>
public sealed class GatedToolRolesTests
{
    [Fact]
    public async Task PlaceBackorder_ExposesItsRolesThroughTheRegisteredFunction_AndRefusesACustomer()
    {
        var settings = new AgentSettings();
        await using var pool = new DatabasePool(settings);
        var functions = new InventoryTools(pool, settings).All().OfType<AIFunction>().ToDictionary(f => f.Name);

        var roles = HitlGate.RolesOf(functions["place_backorder"]);

        roles.Should().BeEquivalentTo(["seller", "admin"]);
        ParkPrechecks.RoleRefusal(settings, "place_backorder", roles, "customer")
            .Should().Be("Your account is not permitted to place backorders; that is limited to admin, seller accounts. I have not submitted anything for approval.");
        ParkPrechecks.RoleRefusal(settings, "place_backorder", roles, "admin").Should().BeNull();
        ParkPrechecks.RoleRefusal(settings, "place_backorder", roles, "seller").Should().BeNull();
    }
}
