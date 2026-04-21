namespace ECommerceAgents.Shared.Context;

/// <summary>
/// Request-scoped identity + conversation context.
/// </summary>
/// <remarks>
/// Mirrors Python's <c>shared/context.py</c> ContextVars. In .NET the
/// equivalent is <see cref="AsyncLocal{T}"/>, which propagates across
/// <c>async</c> boundaries within the same logical flow. Middleware
/// populates the context from JWT claims or the inter-agent auth
/// headers before the route handler runs; tools read it directly
/// instead of threading parameters through call stacks.
/// </remarks>
public static class RequestContext
{
    private static readonly AsyncLocal<string?> _userEmail = new();
    private static readonly AsyncLocal<string?> _userRole = new();
    private static readonly AsyncLocal<string?> _sessionId = new();
    private static readonly AsyncLocal<IReadOnlyList<HistoryEntry>?> _history = new();

    public static string CurrentUserEmail
    {
        get => _userEmail.Value ?? string.Empty;
        set => _userEmail.Value = value;
    }

    public static string CurrentUserRole
    {
        get => _userRole.Value ?? string.Empty;
        set => _userRole.Value = value;
    }

    public static string CurrentSessionId
    {
        get => _sessionId.Value ?? string.Empty;
        set => _sessionId.Value = value;
    }

    public static IReadOnlyList<HistoryEntry> CurrentHistory
    {
        get => _history.Value ?? Array.Empty<HistoryEntry>();
        set => _history.Value = value;
    }

    public static IDisposable Scope(string email, string role, string sessionId, IReadOnlyList<HistoryEntry>? history = null)
    {
        var previous = (Email: _userEmail.Value, Role: _userRole.Value, Session: _sessionId.Value, History: _history.Value);
        _userEmail.Value = email;
        _userRole.Value = role;
        _sessionId.Value = sessionId;
        _history.Value = history ?? Array.Empty<HistoryEntry>();
        return new Disposable(() =>
        {
            _userEmail.Value = previous.Email;
            _userRole.Value = previous.Role;
            _sessionId.Value = previous.Session;
            _history.Value = previous.History;
        });
    }

    private sealed class Disposable(Action dispose) : IDisposable
    {
        private readonly Action _dispose = dispose;

        public void Dispose() => _dispose();
    }
}

/// <summary>One entry in the forwarded conversation history (A2A payload shape).</summary>
public sealed record HistoryEntry(string Role, string Content);
