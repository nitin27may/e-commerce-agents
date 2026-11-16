namespace ECommerceAgents.Shared.Policy;

/// <summary>
/// Finds and loads <c>config/policies/approvals.yaml</c> — the file Python reads — once per process.
/// A missing or invalid file is not an exception here: <see cref="Current"/> is <c>null</c> and callers fail
/// closed (route to a manager, schedule no deadline they cannot compute) rather than invent a policy.
/// </summary>
public static class ApprovalPolicyLocator
{
    private static readonly Lazy<ApprovalPolicy?> Loaded = new(Load);

    /// <summary>The parsed policy, or <c>null</c> when the file cannot be found or is invalid.</summary>
    public static ApprovalPolicy? Current => Loaded.Value;

    /// <summary>The path of <c>approvals.yaml</c>: the repo copy found by walking up from the binaries, else one beside them.</summary>
    public static string? PolicyFile()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            var repoCopy = Path.Combine(dir.FullName, "agents", "python", "config", "policies", "approvals.yaml");
            if (File.Exists(repoCopy)) return repoCopy;
            dir = dir.Parent;
        }
        var beside = Path.Combine(AppContext.BaseDirectory, "config", "policies", "approvals.yaml");
        return File.Exists(beside) ? beside : null;
    }

    private static ApprovalPolicy? Load()
    {
        var path = PolicyFile();
        if (path is null)
        {
            Serilog.Log.Error("approvals.yaml not found; approvals are routed to a manager and carry no SLA timers");
            return null;
        }
        try
        {
            return ApprovalPolicy.LoadFile(path);
        }
        catch (PolicyException ex)
        {
            Serilog.Log.Error(ex, "approvals.yaml is invalid; approvals are routed to a manager and carry no SLA timers");
            return null;
        }
    }
}
