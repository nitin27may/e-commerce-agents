using ECommerceAgents.Shared.Approvals;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Policy;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace ECommerceAgents.Orchestrator.Approvals;

public static class ApprovalServiceCollectionExtensions
{
    /// <summary>
    /// Registers the approvals inbox: the repository, the intake that opens routed approvals, and the service behind the
    /// unified API and both legacy routes. All three read the one <c>approvals.yaml</c> Python reads
    /// (<see cref="ApprovalPolicyLocator"/>); when it is unusable they fail closed (manager route, no timers).
    /// Needs <see cref="DatabasePool"/>, <see cref="AgentSettings"/> and logging already registered.
    /// </summary>
    public static IServiceCollection AddApprovals(this IServiceCollection services)
    {
        services.AddSingleton(sp => new ApprovalRepository(
            sp.GetRequiredService<DatabasePool>(),
            sp.GetRequiredService<AgentSettings>(),
            ApprovalPolicyLocator.Current,
            sp.GetService<ILoggerFactory>()?.CreateLogger<ApprovalRepository>()));
        services.AddSingleton(sp => new ApprovalIntake(
            sp.GetRequiredService<DatabasePool>(),
            sp.GetRequiredService<ApprovalRepository>(),
            ApprovalPolicyLocator.Current,
            sp.GetService<ILoggerFactory>()?.CreateLogger<ApprovalIntake>()));
        services.AddSingleton(sp => new ApprovalsService(
            sp.GetRequiredService<DatabasePool>(),
            sp.GetRequiredService<ApprovalRepository>(),
            sp.GetRequiredService<ApprovalIntake>(),
            ApprovalPolicyLocator.Current,
            sp.GetRequiredService<AgentSettings>(),
            sp,
            sp.GetRequiredService<ILogger<ApprovalsService>>()));
        return services;
    }
}
