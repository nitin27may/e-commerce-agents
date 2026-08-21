using Microsoft.Extensions.AI;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace ECommerceAgents.Shared.Agents;

/// <summary>
/// An <see cref="IChatClient"/> that answers from recorded fixtures instead of calling a
/// model — the .NET twin of Python's <c>shared/replay_client.py</c>.
/// </summary>
/// <remarks>
/// This is what makes an eval suite deterministic and free: the same question always gets
/// the same answer, no credentials are needed, and a scoring regression is attributable to
/// a code change rather than to the model having a different day. Python's CI smoke job
/// runs its entire eval suite this way.
///
/// <b>Keyed on the request, not on call order.</b> <c>FakeChatClient</c> in the test
/// fixtures pops canned strings off a queue, which cannot survive a tool-calling loop
/// where the number of model turns varies. Here the fixture is looked up by a hash of
/// (messages, tools, instructions), so turn N+1 of a loop finds its own recording.
///
/// <b>What gets scrubbed, and why only inside tool results.</b> A tool result carries live
/// database payloads — row ids and timestamps that differ between seeds — straight back
/// into the next turn's messages. Left alone they change the hash and every fixture misses
/// after a reseed. UUIDs and ISO-8601 timestamps are therefore replaced with placeholders
/// *for hashing only*, and only inside <c>tool</c> messages: genuinely different tool
/// *calls* differ in their arguments, which live in the assistant message and are never
/// scrubbed, so two distinct calls cannot collide. The fixture on disk keeps the raw,
/// unscrubbed request, which is what makes a miss debuggable.
/// </remarks>
public sealed class ReplayChatClient : IChatClient
{
    private readonly string _fixturesDir;
    private readonly IChatClient? _recorder;

    /// <param name="recorder">
    /// When supplied, a fixture miss calls this real provider and records the
    /// answer instead of throwing — the twin of Python's <c>RECORD=true</c>.
    /// Null means a miss is fatal, which is the normal mode: a run that can
    /// quietly reach the network is not a deterministic run.
    /// </param>
    public ReplayChatClient(string fixturesDir, IChatClient? recorder = null)
    {
        _fixturesDir = fixturesDir;
        _recorder = recorder;
        Directory.CreateDirectory(_fixturesDir);
    }

    /// <summary>Thrown when no recording matches — never a silently wrong answer.</summary>
    /// <remarks>
    /// Distinct from a generic failure on purpose. Python learned this the hard way: a
    /// missing fixture was caught as a generic exception and scored as an all-zero case,
    /// so CI reported "this agent scores 40%" when the truth was "three recordings are
    /// missing and the agent never ran".
    /// </remarks>
    public sealed class FixtureMissingException(string key, string fixturesDir) : Exception(
        $"No replay fixture {key}.json in {fixturesDir}. Record it, or re-key the corpus if the "
            + "hashing scheme changed — a missing fixture means the agent did not run at all.")
    {
        public string Key { get; } = key;
    }

    public async Task<ChatResponse> GetResponseAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options = null,
        CancellationToken cancellationToken = default
    )
    {
        var key = RequestHash(messages, options);
        var path = Path.Combine(_fixturesDir, $"{key}.json");

        if (!File.Exists(path))
        {
            if (_recorder is null)
            {
                throw new FixtureMissingException(key, _fixturesDir);
            }

            // Record on miss, not on every call. Re-running a recording pass
            // against a populated directory therefore *replays* rather than
            // re-recording — the same semantics as Python, and the same trap:
            // to genuinely re-record a suite, delete its fixtures first.
            var recorded = await _recorder.GetResponseAsync(messages, options, cancellationToken);
            await RecordAsync(messages, options, recorded, cancellationToken);
            return recorded;
        }

        using var doc = JsonDocument.Parse(await File.ReadAllTextAsync(path, cancellationToken));
        return Deserialize(doc.RootElement.GetProperty("response"));
    }

    public async IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options = null,
        [EnumeratorCancellation] CancellationToken cancellationToken = default
    )
    {
        // Replayed as a single chunk. A recording captures what the model said, not the
        // token boundaries it happened to say it in, and no scorer depends on chunking.
        var response = await GetResponseAsync(messages, options, cancellationToken);
        foreach (var message in response.Messages)
        {
            yield return new ChatResponseUpdate(message.Role, message.Contents);
        }
    }

    public object? GetService(Type serviceType, object? serviceKey = null) => null;

    public void Dispose() { }

    /// <summary>
    /// Records a response under the key this client would look it up by.
    /// </summary>
    /// <remarks>
    /// Writes the *unscrubbed* request alongside the response, so a later miss can be
    /// diffed against what was actually recorded rather than guessed at.
    /// </remarks>
    public async Task RecordAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options,
        ChatResponse response,
        CancellationToken ct = default
    )
    {
        var key = RequestHash(messages, options);
        var payload = new
        {
            request = Canonical(messages, options),
            response = Serialize(response),
        };
        await File.WriteAllTextAsync(
            Path.Combine(_fixturesDir, $"{key}.json"),
            JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = true }),
            ct);
    }

    // ─────────────────────── keying ───────────────────────

    internal static string RequestHash(IEnumerable<ChatMessage> messages, ChatOptions? options)
    {
        var canonical = JsonSerializer.Serialize(NormalizeForHash(Canonical(messages, options)));
        return Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(canonical)))[..16];
    }

    private static Dictionary<string, object?> Canonical(IEnumerable<ChatMessage> messages, ChatOptions? options) =>
        new()
        {
            ["messages"] = messages.Select(m => new Dictionary<string, object?>
            {
                ["role"] = m.Role.Value,
                ["text"] = m.Text,
            }).ToList(),
            // Tool *names* only. Descriptions and schemas are prompt-shaped and get
            // reworded without changing behaviour; including them would invalidate the
            // whole corpus on a docstring edit.
            ["tools"] = options?.Tools?.Select(t => t.Name).OrderBy(n => n, StringComparer.Ordinal).ToList(),
            ["instructions"] = options?.Instructions,
        };

    private static readonly System.Text.RegularExpressions.Regex UuidPattern = new(
        @"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        System.Text.RegularExpressions.RegexOptions.Compiled);

    private static readonly System.Text.RegularExpressions.Regex TimestampPattern = new(
        @"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)?|\d{4}-\d{2}(?=\b)",
        System.Text.RegularExpressions.RegexOptions.Compiled);

    private static Dictionary<string, object?> NormalizeForHash(Dictionary<string, object?> canonical)
    {
        if (canonical["messages"] is not List<Dictionary<string, object?>> messages)
        {
            return canonical;
        }

        canonical["messages"] = messages.Select(m =>
        {
            if (m["role"] as string != "tool" || m["text"] is not string text)
            {
                return m;
            }
            return new Dictionary<string, object?>
            {
                ["role"] = m["role"],
                ["text"] = TimestampPattern.Replace(UuidPattern.Replace(text, "<uuid>"), "<ts>"),
            };
        }).ToList();

        return canonical;
    }

    // ─────────────────────── (de)serialization ───────────────────────

    private static object Serialize(ChatResponse response) => new
    {
        messages = response.Messages.Select(m => new
        {
            role = m.Role.Value,
            text = m.Text,
            function_calls = m.Contents.OfType<FunctionCallContent>().Select(c => new
            {
                call_id = c.CallId,
                name = c.Name,
                arguments = c.Arguments,
            }).ToList(),
        }).ToList(),
    };

    private static ChatResponse Deserialize(JsonElement response)
    {
        var messages = new List<ChatMessage>();

        foreach (var m in response.GetProperty("messages").EnumerateArray())
        {
            var role = new ChatRole(m.GetProperty("role").GetString() ?? "assistant");
            var contents = new List<AIContent>();

            if (m.TryGetProperty("text", out var text) && !string.IsNullOrEmpty(text.GetString()))
            {
                contents.Add(new TextContent(text.GetString()!));
            }

            if (m.TryGetProperty("function_calls", out var calls))
            {
                foreach (var call in calls.EnumerateArray())
                {
                    // Restored as real FunctionCallContent, not text. This is the whole
                    // point: the function-invocation layer above executes the local tool
                    // for real, and the next turn is looked up by its own hash — so a
                    // recorded tool loop replays as a loop rather than as a transcript.
                    var arguments = call.TryGetProperty("arguments", out var args) && args.ValueKind == JsonValueKind.Object
                        ? args.EnumerateObject().ToDictionary(p => p.Name, p => (object?)p.Value.ToString())
                        : [];

                    contents.Add(new FunctionCallContent(
                        call.GetProperty("call_id").GetString() ?? "call_0",
                        call.GetProperty("name").GetString() ?? "",
                        arguments));
                }
            }

            messages.Add(new ChatMessage(role, contents));
        }

        return new ChatResponse(messages);
    }
}
