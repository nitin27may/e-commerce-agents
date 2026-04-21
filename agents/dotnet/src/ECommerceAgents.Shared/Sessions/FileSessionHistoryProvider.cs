using System.Text.Json;

namespace ECommerceAgents.Shared.Sessions;

/// <summary>
/// JSONL-file-backed history. Mirrors Python's
/// <c>FileSessionHistoryProvider</c>: one file per session, each line
/// a serialised <see cref="StoredMessage"/>. Handy for dev without a
/// DB.
/// </summary>
public sealed class FileSessionHistoryProvider : ISessionHistoryProvider
{
    private readonly string _directory;

    public FileSessionHistoryProvider(string directory)
    {
        if (string.IsNullOrWhiteSpace(directory))
        {
            throw new ArgumentException("directory is required", nameof(directory));
        }
        _directory = directory;
        Directory.CreateDirectory(_directory);
    }

    private string PathFor(string sessionId)
    {
        // Sanitise path separators so a hostile session_id can't escape
        // the directory.
        var safe = sessionId.Replace('/', '_').Replace('\\', '_');
        return Path.Combine(_directory, $"{safe}.jsonl");
    }

    public async Task<List<StoredMessage>> GetMessagesAsync(string? sessionId, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(sessionId))
        {
            return [];
        }
        var path = PathFor(sessionId);
        if (!File.Exists(path))
        {
            return [];
        }

        var messages = new List<StoredMessage>();
        await foreach (var line in File.ReadLinesAsync(path, ct))
        {
            if (string.IsNullOrWhiteSpace(line)) continue;
            var parsed = JsonSerializer.Deserialize<StoredMessage>(line);
            if (parsed is not null)
            {
                messages.Add(parsed);
            }
        }
        return messages;
    }

    public async Task SaveMessagesAsync(
        string? sessionId,
        IReadOnlyList<StoredMessage> messages,
        CancellationToken ct = default
    )
    {
        if (string.IsNullOrEmpty(sessionId) || messages.Count == 0)
        {
            return;
        }
        var path = PathFor(sessionId);
        await using var stream = new FileStream(
            path,
            FileMode.Append,
            FileAccess.Write,
            FileShare.Read
        );
        await using var writer = new StreamWriter(stream);
        foreach (var msg in messages)
        {
            await writer.WriteLineAsync(JsonSerializer.Serialize(msg));
        }
    }
}
