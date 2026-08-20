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
        var email = RequestContext.CurrentUserEmail;
        if (string.IsNullOrEmpty(email))
        {
            return new AIContext();
        }

        var enriched = await enricher.EnrichAsync(email, cancellationToken);
        return string.IsNullOrEmpty(enriched.UserContext)
            ? new AIContext()
            : new AIContext { Instructions = enriched.UserContext };
    }
}
