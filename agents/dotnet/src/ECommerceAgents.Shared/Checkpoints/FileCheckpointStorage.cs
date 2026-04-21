using System.Text.Json;

namespace ECommerceAgents.Shared.Checkpoints;

/// <summary>
/// JSON-file-per-checkpoint storage. Directory layout:
/// <c>{root}/{workflow_name}/{checkpoint_id}.json</c>.
/// </summary>
public sealed class FileCheckpointStorage : ICheckpointStorage
{
    private readonly string _root;

    public FileCheckpointStorage(string root)
    {
        if (string.IsNullOrWhiteSpace(root))
        {
            throw new ArgumentException("root is required", nameof(root));
        }
        _root = root;
        Directory.CreateDirectory(_root);
    }

    private string DirFor(string workflowName) =>
        Path.Combine(_root, Sanitise(workflowName));

    private string PathFor(string workflowName, string checkpointId) =>
        Path.Combine(DirFor(workflowName), $"{Sanitise(checkpointId)}.json");

    private static string Sanitise(string input) =>
        string.Concat(input.Select(c =>
            c is '/' or '\\' or ':' or '*' or '?' or '"' or '<' or '>' or '|' ? '_' : c
        ));

    public async Task<string> SaveAsync(WorkflowCheckpoint checkpoint, CancellationToken ct = default)
    {
        Directory.CreateDirectory(DirFor(checkpoint.WorkflowName));
        var path = PathFor(checkpoint.WorkflowName, checkpoint.CheckpointId);
        var body = JsonSerializer.Serialize(checkpoint);
        await File.WriteAllTextAsync(path, body, ct);
        return checkpoint.CheckpointId;
    }

    public async Task<WorkflowCheckpoint?> LoadAsync(string checkpointId, CancellationToken ct = default)
    {
        // Walk the workflow dirs looking for a match; the storage is
        // partitioned by workflow so a pure id lookup means scanning.
        if (!Directory.Exists(_root)) return null;
        foreach (var dir in Directory.EnumerateDirectories(_root))
        {
            var path = Path.Combine(dir, $"{Sanitise(checkpointId)}.json");
            if (File.Exists(path))
            {
                var body = await File.ReadAllTextAsync(path, ct);
                return JsonSerializer.Deserialize<WorkflowCheckpoint>(body);
            }
        }
        return null;
    }

    public async Task<IReadOnlyList<string>> ListCheckpointIdsAsync(string workflowName, CancellationToken ct = default)
    {
        var dir = DirFor(workflowName);
        if (!Directory.Exists(dir)) return [];
        var files = Directory.EnumerateFiles(dir, "*.json")
            .Select(f => (Path: f, Written: File.GetLastWriteTimeUtc(f)))
            .OrderByDescending(x => x.Written)
            .Select(x => Path.GetFileNameWithoutExtension(x.Path))
            .ToList();
        // Reload so we return the original (non-sanitised) id.
        var ids = new List<string>(files.Count);
        foreach (var file in files)
        {
            var cp = await LoadAsync(file, ct);
            if (cp is not null) ids.Add(cp.CheckpointId);
        }
        return ids;
    }

    public async Task<WorkflowCheckpoint?> GetLatestAsync(string workflowName, CancellationToken ct = default)
    {
        var dir = DirFor(workflowName);
        if (!Directory.Exists(dir)) return null;
        var newest = Directory.EnumerateFiles(dir, "*.json")
            .OrderByDescending(File.GetLastWriteTimeUtc)
            .FirstOrDefault();
        if (newest is null) return null;
        var body = await File.ReadAllTextAsync(newest, ct);
        return JsonSerializer.Deserialize<WorkflowCheckpoint>(body);
    }

    public async Task<bool> DeleteAsync(string checkpointId, CancellationToken ct = default)
    {
        if (!Directory.Exists(_root)) return false;
        var sanitised = Sanitise(checkpointId) + ".json";
        foreach (var dir in Directory.EnumerateDirectories(_root))
        {
            var path = Path.Combine(dir, sanitised);
            if (File.Exists(path))
            {
                File.Delete(path);
                await Task.CompletedTask;
                return true;
            }
        }
        return false;
    }
}
