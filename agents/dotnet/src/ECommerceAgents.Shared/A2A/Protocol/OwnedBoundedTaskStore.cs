using System.Text.Json;
using A2A;
using AmbientContext = ECommerceAgents.Shared.Context.RequestContext;

namespace ECommerceAgents.Shared.A2A.Protocol;

/// <summary>
/// An in-memory <see cref="ITaskStore"/> that forgets its oldest tasks and answers reads only to the
/// user who made the task. Twin of Python's <c>BoundedTaskStore</c> plus the SDK's own owner scoping,
/// which the .NET SDK's store interface does not have (it passes no caller to the store).
/// </summary>
/// <remarks>
/// <para>
/// <b>Bounded.</b> <c>InMemoryTaskStore</c> keeps every task for the life of the process; a specialist
/// handles a turn per user message, so unbounded it is a slow leak of everyone's conversations. This keeps
/// the newest <see cref="MaxRetainedTasks"/> and deletes the rest. A task is only ever re-read while it is
/// running or just after, so nothing a caller can use is lost. The bound is process-wide (a busy caller can
/// push another's finished tasks out); a <em>running</em> task is bounded separately, by
/// <see cref="ActiveTaskLimiter"/>.
/// </para>
/// <para>
/// <b>Owner-scoped.</b> The owner is the forwarded user's email, read from
/// <see cref="AmbientContext.CurrentUserEmail"/>, which the auth middleware stamped before this call and
/// which flows into the SDK's background execution (an <c>AsyncLocal</c>). Every public read — get, list,
/// delete, and the continue-a-task lookup a <c>SendMessage</c> with a task id performs — answers "not
/// found" for another user's task, so a task id is not a capability.
/// </para>
/// <para>
/// <b>Extension metadata.</b> The SDK does not copy a status update's metadata into the stored task
/// (Python's SDK does), so a blocking <c>SendMessage</c>, which returns the stored task, would lose the
/// steps and usage. The executor publishes them here first (<see cref="MergeMetadata"/>); they are merged,
/// last write winning per key, into the task on every save.
/// </para>
/// </remarks>
public sealed class OwnedBoundedTaskStore(int maxTasks = OwnedBoundedTaskStore.MaxRetainedTasks) : ITaskStore
{
    public const int MaxRetainedTasks = 256;
    private const int DefaultPageSize = 50;

    private sealed class Entry(AgentTask task, string owner)
    {
        public AgentTask Task { get; set; } = task;
        public string Owner { get; } = owner;
        public Dictionary<string, JsonElement> Extensions { get; } = new(StringComparer.Ordinal);
        public long Sequence { get; set; }
    }

    private readonly object _gate = new();
    private readonly Dictionary<string, Entry> _tasks = new(StringComparer.Ordinal);

    // Extension metadata published before the task's first save (the executor enqueues the submitted task
    // and publishes in the same breath; the SDK saves from a queue). Drained into the entry on that save.
    private readonly Dictionary<string, Dictionary<string, JsonElement>> _early = new(StringComparer.Ordinal);
    private long _sequence;

    private static string Owner => AmbientContext.CurrentUserEmail;

    public int Count
    {
        get
        {
            lock (_gate)
            {
                return _tasks.Count;
            }
        }
    }

    /// <summary>Publishes extension metadata for a task, merged into it on every later save.</summary>
    public void MergeMetadata(string taskId, IReadOnlyDictionary<string, JsonElement>? metadata)
    {
        if (metadata is null || metadata.Count == 0)
        {
            return;
        }

        lock (_gate)
        {
            Dictionary<string, JsonElement> target;
            if (_tasks.TryGetValue(taskId, out var entry))
            {
                target = entry.Extensions;
            }
            else
            {
                if (!_early.TryGetValue(taskId, out target!))
                {
                    if (_early.Count >= maxTasks)
                    {
                        _early.Clear();
                    }

                    target = _early[taskId] = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
                }
            }

            foreach (var (key, value) in metadata)
            {
                target[key] = value.Clone();
            }
        }
    }

    public Task<AgentTask?> GetTaskAsync(string taskId, CancellationToken cancellationToken = default)
    {
        lock (_gate)
        {
            return Task.FromResult<AgentTask?>(
                _tasks.TryGetValue(taskId, out var entry) && entry.Owner == Owner ? entry.Task : null);
        }
    }

    public Task SaveTaskAsync(string taskId, AgentTask task, CancellationToken cancellationToken = default)
    {
        lock (_gate)
        {
            if (_tasks.TryGetValue(taskId, out var entry))
            {
                // Never re-owned: only the creator can reach it to save over it (reads are scoped).
                entry.Task = task;
            }
            else
            {
                entry = new Entry(task, Owner);
                _tasks[taskId] = entry;
                if (_early.Remove(taskId, out var early))
                {
                    foreach (var (key, value) in early)
                    {
                        entry.Extensions[key] = value;
                    }
                }
            }

            if (entry.Extensions.Count > 0)
            {
                var merged = task.Metadata is null
                    ? new Dictionary<string, JsonElement>(StringComparer.Ordinal)
                    : new Dictionary<string, JsonElement>(task.Metadata, StringComparer.Ordinal);
                foreach (var (key, value) in entry.Extensions)
                {
                    merged[key] = value;
                }

                task.Metadata = merged;
            }

            entry.Sequence = ++_sequence;
            while (_tasks.Count > maxTasks)
            {
                var oldest = _tasks.MinBy(kv => kv.Value.Sequence).Key;
                _tasks.Remove(oldest);
            }
        }

        return Task.CompletedTask;
    }

    public Task DeleteTaskAsync(string taskId, CancellationToken cancellationToken = default)
    {
        lock (_gate)
        {
            if (_tasks.TryGetValue(taskId, out var entry) && entry.Owner == Owner)
            {
                _tasks.Remove(taskId);
            }
        }

        return Task.CompletedTask;
    }

    public Task<ListTasksResponse> ListTasksAsync(ListTasksRequest request, CancellationToken cancellationToken = default)
    {
        List<AgentTask> mine;
        lock (_gate)
        {
            var owner = Owner;
            mine = _tasks.Values
                .Where(e => e.Owner == owner)
                .Where(e => string.IsNullOrEmpty(request.ContextId) || e.Task.ContextId == request.ContextId)
                .Where(e => request.Status is null || e.Task.Status.State == request.Status)
                .OrderByDescending(e => e.Sequence)
                .Select(e => e.Task)
                .ToList();
        }

        var pageSize = request.PageSize ?? DefaultPageSize;
        var start = int.TryParse(request.PageToken, out var parsed) && parsed >= 0 ? parsed : 0;
        var page = mine.Skip(start).Take(pageSize).ToList();
        var next = start + page.Count < mine.Count ? (start + page.Count).ToString(System.Globalization.CultureInfo.InvariantCulture) : string.Empty;

        return Task.FromResult(new ListTasksResponse
        {
            Tasks = request.IncludeArtifacts == true ? page : page.Select(StripArtifacts).ToList(),
            NextPageToken = next,
            PageSize = page.Count,
            TotalSize = mine.Count,
        });
    }

    private static AgentTask StripArtifacts(AgentTask task) => new()
    {
        Id = task.Id,
        ContextId = task.ContextId,
        Status = task.Status,
        History = task.History,
        Metadata = task.Metadata,
    };
}
