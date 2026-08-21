using ECommerceAgents.Shared.Configuration;
using Microsoft.Extensions.Logging;
using StackExchange.Redis;

namespace ECommerceAgents.Shared.RateLimiting;

/// <summary>
/// Redis-backed sliding-window rate limiting — the .NET twin of Python's
/// <c>shared/rate_limit.py</c>.
/// </summary>
/// <remarks>
/// Redis was provisioned in <c>docker-compose.dotnet.yml</c> and
/// <c>StackExchange.Redis</c> referenced in this project from the start, but
/// nothing in <c>agents/dotnet</c> ever used either. An agentic chat endpoint
/// with no rate limit is an open door for cost abuse — each turn can trigger
/// several LLM calls — and <c>/api/chat</c> and <c>/api/chat/stream</c> both
/// serve anonymous storefront traffic, so a single IP with no account could
/// hit them without limit. See issue #30.
///
/// Algorithm: a sliding-window log per key held in a Redis sorted set. Each
/// request adds a member scored by its own timestamp; the window is then
/// trimmed and counted, and the request recorded only if it is under the
/// limit — all inside one Lua script, so concurrent requests against the same
/// key cannot race past the limit between separate round trips the way a naive
/// read-then-write would. The script is byte-for-byte the one Python runs, so
/// both stacks share a limiter's worth of semantics rather than two
/// approximations of it.
/// </remarks>
public sealed class SlidingWindowRateLimiter
{
    // KEYS[1] = the rate-limit key   ARGV[1] = now (ms)   ARGV[2] = window (ms)
    // ARGV[3] = max requests         ARGV[4] = unique member id for this request
    // The member id avoids score collisions when two requests land in the same
    // millisecond, which would otherwise silently overwrite one another.
    private const string SlidingWindowScript = """
        local key = KEYS[1]
        local now_ms = tonumber(ARGV[1])
        local window_ms = tonumber(ARGV[2])
        local max_requests = tonumber(ARGV[3])
        local member = ARGV[4]

        redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
        local count = redis.call('ZCARD', key)

        if count < max_requests then
            redis.call('ZADD', key, now_ms, member)
            redis.call('PEXPIRE', key, window_ms)
            return {1, count + 1}
        end

        return {0, count}
        """;

    private readonly AgentSettings _settings;
    private readonly ILogger<SlidingWindowRateLimiter> _logger;
    private readonly Lazy<Task<IConnectionMultiplexer?>> _redis;

    public SlidingWindowRateLimiter(AgentSettings settings, ILogger<SlidingWindowRateLimiter> logger)
    {
        _settings = settings;
        _logger = logger;
        _redis = new Lazy<Task<IConnectionMultiplexer?>>(ConnectAsync);
    }

    private async Task<IConnectionMultiplexer?> ConnectAsync()
    {
        try
        {
            return await ConnectionMultiplexer.ConnectAsync(_settings.RedisUrl);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "rate_limit.redis_unavailable url={Url}", _settings.RedisUrl);
            return null;
        }
    }

    /// <summary>
    /// Returns <see langword="true"/> when the request is allowed.
    /// </summary>
    /// <remarks>
    /// Fails <b>open</b> when Redis is unreachable: an outage of the rate
    /// limiter must not take chat down with it. That is the opposite of
    /// <see cref="Middleware.HitlGate"/>'s fail-closed posture, and
    /// deliberately so — one protects spend, the other protects money
    /// leaving the business.
    /// </remarks>
    public async Task<bool> TryAcquireAsync(string key, CancellationToken ct = default)
    {
        if (!_settings.RateLimitEnabled)
        {
            return true;
        }

        var mux = await _redis.Value;
        if (mux is null || !mux.IsConnected)
        {
            return true;
        }

        var nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var windowMs = (long)(_settings.RateLimitWindowSeconds * 1000);
        var member = $"{nowMs}:{Guid.NewGuid():N}"[..Math.Min(32, $"{nowMs}:{Guid.NewGuid():N}".Length)];

        try
        {
            var result = await mux.GetDatabase().ScriptEvaluateAsync(
                SlidingWindowScript,
                [key],
                [nowMs, windowMs, _settings.RateLimitMaxRequests, member]
            );

            var values = (RedisValue[]?)result;
            var allowed = values is { Length: > 0 } && (long)values[0] == 1;
            if (!allowed)
            {
                _logger.LogWarning(
                    "rate_limit.exceeded key={Key} max={Max} window_s={Window}",
                    key, _settings.RateLimitMaxRequests, _settings.RateLimitWindowSeconds
                );
            }
            return allowed;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "rate_limit.check_failed key={Key} — allowing request", key);
            return true;
        }
    }

    /// <summary>
    /// Per-user when we know who is calling, per-IP otherwise — anonymous
    /// storefront traffic is exactly the case that needs limiting most.
    /// </summary>
    public static string KeyFor(string? userEmail, string? clientIp) =>
        string.IsNullOrWhiteSpace(userEmail) || userEmail == "anonymous"
            ? $"ratelimit:chat:ip:{clientIp ?? "unknown"}"
            : $"ratelimit:chat:user:{userEmail}";
}
