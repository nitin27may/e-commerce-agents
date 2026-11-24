using System.Text.Json;
using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Logging;
using ECommerceAgents.Shared.Middleware;
using Microsoft.Extensions.Logging;

namespace ECommerceAgents.Shared.Approvals;

/// <summary>What the host does with a gated call: <c>Ask</c> and <c>Declined</c> carry the sentence the customer reads.</summary>
public enum ConfirmationKind
{
    Ask,
    Confirmed,
    Declined,
}

public sealed record ConfirmationOutcome(ConfirmationKind Kind, string? Sentence = null, Guid? ConfirmationId = null);

/// <summary>
/// The <c>customer_confirmations</c> record (migration 0016): one open question per (user, session, tool, normalised arguments).
/// The .NET twin of <c>evaluate</c> / <c>latest_user_message</c> / <c>reopen</c> in Python's <c>shared/customer_confirmation.py</c>,
/// over the same table, so a question asked through one stack can be answered through the other.
/// </summary>
public sealed class CustomerConfirmationRepository
{
    private readonly DatabasePool _pool;
    private readonly AgentSettings _settings;
    private readonly ILogger? _logger;

    public CustomerConfirmationRepository(DatabasePool pool, AgentSettings settings, ILogger? logger = null)
    {
        _pool = pool ?? throw new ArgumentNullException(nameof(pool));
        _settings = settings ?? throw new ArgumentNullException(nameof(settings));
        _logger = logger;
    }

    /// <summary>
    /// What the caller last typed in this conversation, from <c>messages</c>, scoped to the caller. The text a specialist is
    /// <em>sent</em> is composed by the orchestrator's model and cannot vouch for the user, so <paramref name="fallback"/> is used
    /// only when there is no stored conversation to read (a direct caller whose message really is the user's).
    /// </summary>
    public async Task<string?> LatestUserMessageAsync(string userEmail, string? sessionId, string? fallback)
    {
        if (Guid.TryParse(sessionId, out var conversation))
        {
            await using var conn = await _pool.OpenAsync();
            var content = await conn.QueryFirstOrDefaultAsync<string>(
                @"SELECT m.content FROM messages m
                    JOIN conversations c ON c.id = m.conversation_id
                    JOIN users u ON u.id = c.user_id
                   WHERE c.id = @conversation AND lower(u.email) = lower(@email) AND m.role = 'user'
                   ORDER BY m.created_at DESC, m.id DESC LIMIT 1",
                new { conversation, email = userEmail });
            if (content is not null)
            {
                return content;
            }
        }

        return fallback;
    }

    /// <summary>
    /// Decides whether this gated call has been confirmed by the customer, asking if it has not.
    /// No open question for this exact action: open one (it expires after the TTL) and ask. An open one and a latest message that did
    /// not raise it and is an explicit yes: consume it. A clear no: close it. Anything else: ask again, same deadline. Past its
    /// deadline: closed, and a new question opens. Throws whatever the database throws; the caller fails closed.
    /// </summary>
    public async Task<ConfirmationOutcome> EvaluateAsync(
        string userEmail, string? sessionId, string toolName, JsonElement toolInput, string? userMessage)
    {
        var key = CustomerConfirmation.ArgsKey(toolName, toolInput);
        var email = userEmail.ToLowerInvariant();
        var session = sessionId ?? "";
        var latest = await LatestUserMessageAsync(userEmail, sessionId, userMessage);
        var latestNorm = CustomerConfirmation.NormalizeText(latest);
        var prompt = CustomerConfirmation.Prompt(toolName, toolInput);

        await using var conn = await _pool.OpenAsync();
        await using var tx = await conn.BeginTransactionAsync();
        await conn.ExecuteAsync(
            "SELECT pg_advisory_xact_lock(hashtextextended(@lockKey, 0))",
            new { lockKey = $"confirm:{email}:{session}:{toolName}:{key}" }, tx);
        var row = await conn.QueryFirstOrDefaultAsync(
            @"SELECT id, asked_by_message, expires_at <= now() AS expired FROM customer_confirmations
               WHERE lower(user_email) = @email AND session_id = @session AND tool_name = @tool AND args_key = @key
                 AND status = 'pending'",
            new { email, session, tool = toolName, key }, tx);

        if (row is not null && (bool)row.expired)
        {
            await conn.ExecuteAsync(
                "UPDATE customer_confirmations SET status = 'expired', resolved_at = now() WHERE id = @id", new { id = (Guid)row.id }, tx);
            row = null;
        }

        if (row is not null)
        {
            var id = (Guid)row.id;
            if (latestNorm.Length > 0 && latestNorm != (string)row.asked_by_message)
            {
                if (CustomerConfirmation.IsAffirmative(latest))
                {
                    await conn.ExecuteAsync(
                        "UPDATE customer_confirmations SET status = 'confirmed', resolved_at = now() WHERE id = @id", new { id }, tx);
                    await tx.CommitAsync();
                    return new ConfirmationOutcome(ConfirmationKind.Confirmed, null, id);
                }

                if (CustomerConfirmation.IsDecline(latest))
                {
                    await conn.ExecuteAsync(
                        "UPDATE customer_confirmations SET status = 'declined', resolved_at = now() WHERE id = @id", new { id }, tx);
                    await tx.CommitAsync();
                    return new ConfirmationOutcome(ConfirmationKind.Declined, CustomerConfirmation.DeclinedReply, id);
                }
            }

            await tx.CommitAsync();
            return new ConfirmationOutcome(ConfirmationKind.Ask, prompt, id);
        }

        var created = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO customer_confirmations
                  (user_email, session_id, tool_name, args_key, tool_input, asked_by_message, expires_at)
              VALUES (@userEmail, @session, @tool, @key, @input::jsonb, @asked, now() + make_interval(secs => @ttl))
              RETURNING id",
            new
            {
                userEmail,
                session,
                tool = toolName,
                key,
                input = toolInput.GetRawText(),
                asked = latestNorm,
                ttl = (double)_settings.CustomerConfirmationTtlSeconds,
            }, tx);
        await tx.CommitAsync();
        _logger?.LogInformation("confirmation.asked tool={Tool} confirmation_id={Id}", LogSafe.Clean(toolName), created);
        return new ConfirmationOutcome(ConfirmationKind.Ask, prompt, created);
    }

    /// <summary>
    /// Puts a consumed confirmation back, best effort: the call it allowed could not be parked, and the customer's yes should still
    /// stand when they ask again within the deadline.
    /// </summary>
    public async Task ReopenAsync(Guid confirmationId)
    {
        try
        {
            await using var conn = await _pool.OpenAsync();
            await conn.ExecuteAsync(
                "UPDATE customer_confirmations SET status = 'pending', resolved_at = NULL WHERE id = @id AND status = 'confirmed'",
                new { id = confirmationId });
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "confirmation.reopen_failed confirmation_id={Id}", confirmationId);
        }
    }
}
