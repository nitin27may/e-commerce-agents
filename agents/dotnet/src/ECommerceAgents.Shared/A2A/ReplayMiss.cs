using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;

namespace ECommerceAgents.Shared.A2A;

/// <summary>
/// How a replay miss (<c>LLM_PROVIDER=replay</c>, no recording for the request) is recognised and told to a user.
/// Twin of Python's <c>shared/replay_miss.py</c>: the same wire markers, the same user-facing contract.
/// </summary>
/// <remarks>
/// A miss can happen in this process (the orchestrator's own model call) or in a specialist, and must reach the
/// browser as the same thing either way: a plain message, never an apology and never tool-result text the
/// orchestrator's model would then answer (and miss on again). Nothing about the missed request (hash, prompt,
/// fixture path) is ever on the wire or in a log line.
///
/// The wire between processes, legacy: <c>POST /message:send</c> answers HTTP 424 with <c>{"error":"replay_miss"}</c>;
/// <c>POST /message:stream</c> sends an <c>event: replay_miss</c> frame then <c>[DONE]</c>. A2A 1.0: a failed task
/// carrying the <c>replay-miss/v1</c> metadata (see <see cref="Protocol.A2AWire.ReplayMissExtensionUri"/>).
/// </remarks>
public static class ReplayMiss
{
    public const string EventName = "replay_miss";
    public const int Status = 424;
    public const string Marker = "replay_miss";

    /// <summary>The body of a legacy 424 and the data of a legacy <c>replay_miss</c> frame between processes.</summary>
    public static object WireBody => new { error = Marker };

    /// <summary>
    /// The text shown to a user whose prompt missed. Python reads the demo tour manifest's <c>miss_message</c> when
    /// one is configured; the tour is not ported (docs/parity-matrix.md row 31), so this stack always uses
    /// Python's own fallback text.
    /// </summary>
    public const string DefaultMessage =
        "This demo replays recorded model responses, and this prompt was not recorded. "
        + "Try one of the suggested prompts instead.";

    /// <summary>The structured body the browser receives: the <c>replay_miss</c> frame's data and <c>/api/chat</c>'s field.</summary>
    public static Dictionary<string, string> Payload() => new() { ["message"] = DefaultMessage };

    /// <summary>
    /// Whether THIS process's own model provider is <c>replay</c>. The one gate every miss signal on the orchestrator side passes
    /// through (twin of Python's <c>replay_mode_active</c>). A 424 or a <c>replay_miss</c> frame means "no recording" only to a
    /// process that is itself replaying: elsewhere a proxy's 424, or a misconfigured or hostile specialist, would make a production
    /// orchestrator delete the user's message, drop the answer and show the demo text. Outside replay mode those signals are an
    /// ordinary specialist failure.
    /// </summary>
    public static bool IsActive(AgentSettings settings) =>
        string.Equals(settings.LlmProvider?.Trim(), "replay", StringComparison.OrdinalIgnoreCase);

    /// <summary><see cref="IsMiss(Exception?)"/>, but only for a process that is replaying (<see cref="IsActive"/>).</summary>
    public static bool IsMiss(Exception? ex, AgentSettings settings) => IsActive(settings) && IsMiss(ex);

    /// <summary>True when <paramref name="ex"/>, or anything it wraps, is a replay miss (here or in a specialist).</summary>
    public static bool IsMiss(Exception? ex)
    {
        for (var current = ex; current is not null; current = current.InnerException)
        {
            if (current is ReplayChatClient.FixtureMissingException or SpecialistReplayMissException)
            {
                return true;
            }

            if (current is AggregateException aggregate && aggregate.InnerExceptions.Any(IsMiss))
            {
                return true;
            }
        }

        return false;
    }
}

/// <summary>
/// A specialist (another process) reported a replay miss. The message is fixed on purpose: it never carries the
/// specialist's text.
/// </summary>
public sealed class SpecialistReplayMissException()
    : Exception("a specialist agent has no recorded response for this request");
