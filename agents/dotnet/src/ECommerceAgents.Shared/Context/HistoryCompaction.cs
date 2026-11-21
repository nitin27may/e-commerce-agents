using Serilog;

namespace ECommerceAgents.Shared.Context;

/// <summary>
/// Group-preserving trim of rehydrated conversation history (B-15). Twin of Python's
/// <c>shared/history_compaction.py</c>.
/// </summary>
/// <remarks>
/// MAF's <c>CompactionProvider</c> treats each message as a group, so a window of N can start on an assistant
/// reply and cut a turn in half. Here a group is a <c>user</c> message together with every message up to the
/// next user message (assistant text and any tool call / tool result pair), kept or dropped whole, so a tool
/// result is never left without its call. Messages before the first user message form their own leading
/// group, which is therefore the first to go. Off (<c>keepLastGroups &lt;= 0</c>) returns the input list
/// itself, so the model request is byte-identical.
/// </remarks>
public static class HistoryCompaction
{
    /// <summary>Splits <paramref name="history"/> into groups, each starting at a <c>user</c> message.</summary>
    public static List<List<HistoryEntry>> Group(IEnumerable<HistoryEntry> history)
    {
        var groups = new List<List<HistoryEntry>>();
        foreach (var message in history)
        {
            if (message.Role == "user" || groups.Count == 0)
            {
                groups.Add([]);
            }

            groups[^1].Add(message);
        }

        return groups;
    }

    /// <summary>Keeps only the last <paramref name="keepLastGroups"/> groups; <c>&lt;= 0</c> disables it.</summary>
    public static List<HistoryEntry> Compact(List<HistoryEntry> history, int keepLastGroups)
    {
        if (history.Count == 0 || keepLastGroups <= 0)
        {
            return history;
        }

        var groups = Group(history);
        if (groups.Count <= keepLastGroups)
        {
            return history;
        }

        var kept = groups.Skip(groups.Count - keepLastGroups).SelectMany(g => g).ToList();
        Log.Information(
            "history.compacted groups_before={GroupsBefore} groups_kept={GroupsKept} messages_before={MessagesBefore} messages_after={MessagesAfter}",
            groups.Count, keepLastGroups, history.Count, kept.Count);
        return kept;
    }
}
