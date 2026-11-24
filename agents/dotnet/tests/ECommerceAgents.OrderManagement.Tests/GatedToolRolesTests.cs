using ECommerceAgents.OrderManagement.Tools;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Middleware;
using FluentAssertions;
using Microsoft.Extensions.AI;
using Xunit;

namespace ECommerceAgents.OrderManagement.Tests;

/// <summary>
/// The gate's role layer reads <c>[RequiresRole]</c> off the registered tool function (<see cref="HitlGate.RolesOf"/>). These pin that
/// the order tools expose it through the same wrapper the pipeline sees, so a pre-park role refusal matches the tool's own guard.
/// </summary>
public sealed class GatedToolRolesTests
{
    [Fact]
    public async Task CancelAndModify_ExposeTheirRolesThroughTheRegisteredFunction()
    {
        var settings = new AgentSettings();
        await using var pool = new DatabasePool(settings);
        var functions = new OrderTools(pool, settings).All().OfType<AIFunction>().ToDictionary(f => f.Name);

        HitlGate.RolesOf(functions["cancel_order"]).Should().BeEquivalentTo(["customer", "seller"]);
        HitlGate.RolesOf(functions["modify_order"]).Should().BeEquivalentTo(["customer", "seller"]);
        HitlGate.RolesOf(functions["get_order_details"]).Should().BeNull();
    }
}
