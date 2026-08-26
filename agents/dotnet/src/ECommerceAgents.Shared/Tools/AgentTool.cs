using System.Text;
using Microsoft.Extensions.AI;

namespace ECommerceAgents.Shared.Tools;

/// <summary>
/// Registers a tool under the name the <em>shared prompt corpus</em> uses, which is
/// Python's snake_case, not C#'s PascalCase.
/// </summary>
/// <remarks>
/// <para>
/// This exists because the tool name is not an implementation detail — it is a
/// wire contract shared with the model, and in this repo it is shared with the
/// <em>other stack</em> as well. Both stacks are driven by one prompt corpus
/// (<c>agents/python/config/prompts/</c>), which the .NET Dockerfiles ship
/// verbatim:
/// </para>
/// <code>
/// COPY --chown=dotnet:dotnet agents/python/config ./agents/python/config
/// </code>
/// <para>
/// Those prompts name tools the way Python declares them —
/// <c>call_specialist_agent</c>, <c>get_order_details</c>,
/// <c>search_products</c>. Registering the C# equivalents as
/// <c>CallSpecialistAgent</c> / <c>GetOrderDetails</c> / <c>SearchProducts</c>
/// means the model is <em>told</em> about one name and <em>offered</em> another,
/// on every single turn.
/// </para>
/// <para>
/// That is not theoretical. It is what broke the .NET orchestrator completely:
/// primed by a snake_case corpus, the model emitted <c>agent_name</c>, the
/// binder wanted <c>agentName</c>, and every routed request failed with
/// <c>"The arguments dictionary is missing a value for the required parameter
/// 'agentName'"</c>. The stack built, all twelve containers reported healthy,
/// login worked, and no question could be answered. See plan 16 F1.
/// </para>
/// <para>
/// Auditing the rest found the same mismatch on <b>39 of 46</b> registered
/// tools. The orchestrator's was fatal because routing dies outright; the other
/// 38 degrade silently, which is worse to find. So the naming rule lives here,
/// in one place, rather than being restated correctly 46 times and wrongly once.
/// </para>
/// <para>
/// <b>Use <see cref="Create"/> for every tool registration.</b> Calling
/// <c>AIFunctionFactory.Create(fn, nameof(fn))</c> directly reintroduces the bug;
/// <c>ToolNamingTests</c> asserts that no source file does.
/// </para>
/// </remarks>
public static class AgentTool
{
    /// <summary>
    /// Creates an <see cref="AIFunction"/> named in the shared corpus's convention.
    /// </summary>
    /// <param name="method">The tool implementation.</param>
    /// <param name="pascalName">
    /// The C# member name, normally passed as <c>nameof(TheMethod)</c>. Converted
    /// to snake_case before registration.
    /// </param>
    public static AIFunction Create(Delegate method, string pascalName) =>
        AIFunctionFactory.Create(method, ToSnakeCase(pascalName));

    /// <summary>
    /// Converts a PascalCase member name to the snake_case name Python declares.
    /// </summary>
    /// <remarks>
    /// Consecutive capitals are treated as one acronym, so <c>GetA2AStatus</c>
    /// becomes <c>get_a2a_status</c> rather than <c>get_a_2_a_status</c>. Every
    /// one of this repo's 46 tool names round-trips to the matching Python
    /// function name under this rule — verified in <c>ToolNamingTests</c>, which
    /// pins the whole list rather than trusting the transform in the abstract.
    /// </remarks>
    public static string ToSnakeCase(string pascalName)
    {
        if (string.IsNullOrEmpty(pascalName))
        {
            return pascalName;
        }

        var builder = new StringBuilder(pascalName.Length + 8);

        for (int i = 0; i < pascalName.Length; i++)
        {
            char current = pascalName[i];

            if (char.IsUpper(current) && i > 0)
            {
                // A digit does NOT end a word here: "A2A" is one acronym, so
                // `GetA2AStatus` must give `get_a2a_status`, not `get_a2_a_status`.
                // Treating a digit as a boundary is the obvious implementation and
                // is wrong for the one acronym this repo actually uses.
                bool previousIsLower = char.IsLower(pascalName[i - 1]);
                bool endsAcronym = i + 1 < pascalName.Length && char.IsLower(pascalName[i + 1]);

                if (previousIsLower || endsAcronym)
                {
                    builder.Append('_');
                }
            }

            builder.Append(char.ToLowerInvariant(current));
        }

        return builder.ToString();
    }
}
