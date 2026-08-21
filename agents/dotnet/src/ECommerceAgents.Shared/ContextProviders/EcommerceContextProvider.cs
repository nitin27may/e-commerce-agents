using ECommerceAgents.Shared.Context;
using Microsoft.Agents.AI;

namespace ECommerceAgents.Shared.ContextProviders;

/// <summary>
/// Attaches <see cref="ContextEnricher"/>'s <c>user_context</c> block to each
/// agent run — the .NET twin of Python's
/// <c>ECommerceContextProvider.before_run</c>. <see cref="ContextEnricher"/>
/// itself was previously wired to nothing in production (see issue #12); this
/// is the adapter that makes it attachable via
/// <see cref="Microsoft.Agents.AI.ChatClientAgentOptions.AIContextProviders"/>.
/// </summary>
/// <remarks>
/// Deriving from <see cref="AIContextProvider"/> rather than the narrower
/// <see cref="MessageAIContextProvider"/> because
/// <c>ChatClientAgentOptions.AIContextProviders</c> is typed
/// <c>IList&lt;AIContextProvider&gt;</c> — the construction-time attachment
/// point <see cref="Agents.SpecialistAgentFactory"/> already owns, so no
/// separate <see cref="AIAgentBuilder"/> stage is needed for this one.
/// </remarks>
public sealed class EcommerceContextProvider(ContextEnricher enricher) : AIContextProvider
{
    protected override async ValueTask<AIContext> InvokingCoreAsync(
        InvokingContext context,
        CancellationToken cancellationToken
    )
    {
        // Always build on the context MAF hands in, never return a fresh one.
        //
        // Per AIContextProvider's contract, "the first AIContextProvider in the
        // invocation pipeline will receive an instance that already contains the
        // caller provided messages that will be used by the agent", and
        // AIContext.Tools "may modify or replace the existing tools" — meaning a
        // null Tools on the returned context clears them. So returning
        // `new AIContext { Instructions = ... }` silently threw away the user's
        // message, the agent's own system prompt, and every registered tool,
        // leaving the model with nothing but this provider's text and no way to
        // call call_specialist_agent. Mutate and return the same instance.
        var aiContext = context.AIContext;

        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return aiContext;
        }

        var enriched = await enricher.EnrichAsync(email, cancellationToken);
        if (string.IsNullOrEmpty(enriched.UserContext))
        {
            return aiContext;
        }

        // Append rather than assign: a later provider (or MAF itself) may have
        // already put instructions here, and this block is additive context,
        // not a replacement persona — the same semantics as Python's
        // `context.extend_instructions(...)`.
        aiContext.Instructions = string.IsNullOrEmpty(aiContext.Instructions)
            ? enriched.UserContext
            : $"{aiContext.Instructions}\n\n{enriched.UserContext}";

        return aiContext;
    }
}
