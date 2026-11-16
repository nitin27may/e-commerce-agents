using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.DependencyInjection;
using Npgsql;
using StackExchange.Redis;

namespace ECommerceAgents.Shared.Health;

/// <summary>
/// <c>GET /ready</c> — readiness, as distinct from <c>/health</c> liveness (#102).
/// The .NET twin of Python's <c>shared/readiness.py</c>; the JSON shape is identical.
/// </summary>
/// <remarks>
/// <c>/health</c> stays static ("is the process up"). <c>/ready</c> answers "can this
/// service do its job right now", which is what compose's <c>service_healthy</c> wants:
/// <list type="bullet">
/// <item><c>database</c> — <c>SELECT 1</c> over a short-lived private connection, 2 s timeout. Critical.</item>
/// <item><c>migrations</c> — <c>schema_migrations</c> exists and has a row, i.e. the schema was
/// applied by <c>scripts/migrate.py</c>. Critical.</item>
/// <item><c>redis</c> — <c>PING</c>, 2 s timeout. Reported but <b>not</b> critical: rate
/// limiting fails open, so an unreachable Redis must not take the stack down.</item>
/// </list>
/// Body: <c>{"status","service","checks":{name:{"ok","critical","detail"}}}</c>; HTTP 200 with
/// <c>ready</c>, 503 with <c>not_ready</c>. <c>detail</c> carries an exception type name, never
/// its message — a connection error's text can include the connection string. The endpoint is
/// unauthenticated, so <c>migrations</c> reports ok / not ok only, never the migration version.
///
/// Every probe opens fresh Postgres connections, so hammering the public endpoint could exhaust
/// <c>max_connections</c>. The result is cached briefly and at most one probe runs at a time
/// (<see cref="ReadinessCache"/>); concurrent callers wait for it and share its result.
/// </remarks>
public static class ReadinessEndpoint
{
    public static readonly TimeSpan CheckTimeout = TimeSpan.FromSeconds(2);

    /// <summary>
    /// How long a probe result is reused: short enough that compose's 5 s healthcheck and a load
    /// balancer still see a recovery within a poll or two, long enough that a flood costs one
    /// probe per window rather than one per request.
    /// </summary>
    public static readonly TimeSpan CacheTtl = TimeSpan.FromSeconds(1.5);

    // One multiplexer per process: building one per probe would reconnect every 5 s. With
    // AbortOnConnectFail=false it keeps retrying in the background, so a Redis that comes up
    // later flips the check to ok without a restart.
    private static readonly Dictionary<string, Lazy<Task<IConnectionMultiplexer?>>> Redis = new();

    public static IEndpointRouteBuilder MapReadiness(this IEndpointRouteBuilder routes, string service)
    {
        var cache = new ReadinessCache(CacheTtl);
        routes.MapGet("/ready", async (HttpContext http) =>
        {
            var settings = http.RequestServices.GetRequiredService<AgentSettings>();
            // The probe itself is not tied to this request's token: it is shared with every caller
            // waiting on it, so one client hanging up must not abort the result for the others.
            var (status, body) = await cache.GetAsync(
                () => RunAsync(settings, service, CheckTimeout), http.RequestAborted);
            return Results.Json(body, statusCode: status);
        });
        return routes;
    }

    public static async Task<(int Status, object Body)> RunAsync(
        AgentSettings settings, string service, TimeSpan timeout, CancellationToken ct = default)
    {
        var dbProbe = ProbeDatabaseAsync(settings, timeout, ct);
        var redisProbe = CheckRedisAsync(settings, timeout);
        await Task.WhenAll(dbProbe, redisProbe);
        var (db, migrations) = dbProbe.Result;

        var checks = new Dictionary<string, object>
        {
            ["database"] = db,
            ["migrations"] = migrations,
            ["redis"] = redisProbe.Result,
        };
        var ready = db.Ok && migrations.Ok; // redis is not critical
        var body = new { status = ready ? "ready" : "not_ready", service, checks };
        return (ready ? 200 : 503, body);
    }

    public sealed record CheckResult(bool Ok, bool Critical, string Detail);

    /// <summary>
    /// The <c>database</c> and <c>migrations</c> checks over one short-lived, unpooled connection.
    /// </summary>
    /// <remarks>
    /// Deliberately not the service's pool, and bounded by a hard <c>WaitAsync</c> rather than by
    /// cancellation alone. Probing through a pooled connection into a database that stopped
    /// answering (a paused container, a dropped network) did not return within 30 s — found by
    /// pausing the Postgres container under a running orchestrator — because cancelling the
    /// command waits on the dead server. This way the probe answers on its own clock; the abandoned
    /// connection times itself out in the background.
    /// </remarks>
    private static async Task<(CheckResult Database, CheckResult Migrations)> ProbeDatabaseAsync(
        AgentSettings settings, TimeSpan timeout, CancellationToken ct)
    {
        var seconds = (int)Math.Ceiling(timeout.TotalSeconds);
        var cs = new NpgsqlConnectionStringBuilder(PostgresConnectionString.FromUrl(settings.DatabaseUrl))
        {
            Pooling = false,
            Timeout = seconds,
            CommandTimeout = seconds,
            CancellationTimeout = 500,
        }.ConnectionString;

        async Task<(CheckResult, CheckResult)> Run()
        {
            await using var conn = new NpgsqlConnection(cs);
            await conn.OpenAsync(ct);
            await using (var ping = new NpgsqlCommand("SELECT 1", conn))
            {
                await ping.ExecuteScalarAsync(ct);
            }
            var database = new CheckResult(true, true, "ok");
            try
            {
                await using var cmd = new NpgsqlCommand("SELECT count(*) FROM schema_migrations", conn);
                await using var reader = await cmd.ExecuteReaderAsync(ct);
                if (!await reader.ReadAsync(ct) || reader.GetInt64(0) == 0)
                {
                    return (database, new CheckResult(false, true, "schema_migrations is empty"));
                }
                return (database, new CheckResult(true, true, "ok"));
            }
            catch (PostgresException ex)
            {
                // 42P01: scripts/migrate.py has not run against this database.
                return (database, new CheckResult(false, true, ex.GetType().Name));
            }
        }

        try
        {
            return await Run().WaitAsync(timeout, ct);
        }
        catch (TimeoutException)
        {
            var slow = new CheckResult(false, true, $"timed out after {timeout.TotalSeconds:g}s");
            return (slow, slow);
        }
        catch (Exception ex) when (!ct.IsCancellationRequested)
        {
            var failed = new CheckResult(false, true, ex.GetType().Name);
            return (failed, failed);
        }
    }

    private static async Task<CheckResult> CheckRedisAsync(AgentSettings settings, TimeSpan timeout)
    {
        try
        {
            var mux = await GetRedisAsync(settings.RedisUrl, timeout).WaitAsync(timeout);
            if (mux is null || !mux.IsConnected)
            {
                return new CheckResult(false, false, "not connected");
            }
            await mux.GetDatabase().PingAsync().WaitAsync(timeout);
            return new CheckResult(true, false, "ok");
        }
        catch (TimeoutException)
        {
            return new CheckResult(false, false, $"timed out after {timeout.TotalSeconds:g}s");
        }
        catch (Exception ex)
        {
            return new CheckResult(false, false, ex.GetType().Name);
        }
    }

    private static Task<IConnectionMultiplexer?> GetRedisAsync(string url, TimeSpan timeout)
    {
        Lazy<Task<IConnectionMultiplexer?>> lazy;
        lock (Redis)
        {
            if (!Redis.TryGetValue(url, out lazy!))
            {
                lazy = new Lazy<Task<IConnectionMultiplexer?>>(async () =>
                {
                    var options = ConfigurationOptions.Parse(url);
                    options.AbortOnConnectFail = false;
                    options.ConnectTimeout = (int)timeout.TotalMilliseconds;
                    options.SyncTimeout = (int)timeout.TotalMilliseconds;
                    options.AsyncTimeout = (int)timeout.TotalMilliseconds;
                    return await ConnectionMultiplexer.ConnectAsync(options);
                });
                Redis[url] = lazy;
            }
        }
        return lazy.Value;
    }
}

/// <summary>
/// Reuses a readiness result for a short TTL and lets only one probe run at a time. Callers that
/// arrive while a probe is running wait on the gate and then find the fresh result, so N
/// concurrent requests cost one probe.
/// </summary>
public sealed class ReadinessCache(TimeSpan ttl)
{
    private readonly SemaphoreSlim _gate = new(1, 1);
    private (int Status, object Body)? _value;
    private long _expiresAtTicks;

    private bool Fresh => _value is not null && Environment.TickCount64 < Interlocked.Read(ref _expiresAtTicks);

    public async Task<(int Status, object Body)> GetAsync(
        Func<Task<(int Status, object Body)>> probe, CancellationToken ct = default)
    {
        if (Fresh)
        {
            return _value!.Value;
        }

        await _gate.WaitAsync(ct);
        try
        {
            if (Fresh)
            {
                return _value!.Value;
            }

            _value = await probe();
            Interlocked.Exchange(ref _expiresAtTicks, Environment.TickCount64 + (long)ttl.TotalMilliseconds);
            return _value.Value;
        }
        finally
        {
            _gate.Release();
        }
    }
}
