using Dapper;
using ECommerceAgents.Shared.Checkpoints;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

public sealed class InMemoryCheckpointStorageTests
{
    [Fact]
    public async Task SaveLoadDelete_Roundtrip()
    {
        var store = new InMemoryCheckpointStorage();
        var cp = new WorkflowCheckpoint(
            Guid.NewGuid().ToString(),
            "wf-a",
            DateTimeOffset.UtcNow,
            "{\"k\":1}"
        );
        await store.SaveAsync(cp);
        var loaded = await store.LoadAsync(cp.CheckpointId);
        loaded.Should().Be(cp);

        (await store.ListCheckpointIdsAsync("wf-a")).Should().Contain(cp.CheckpointId);
        (await store.GetLatestAsync("wf-a"))!.CheckpointId.Should().Be(cp.CheckpointId);

        (await store.DeleteAsync(cp.CheckpointId)).Should().BeTrue();
        (await store.LoadAsync(cp.CheckpointId)).Should().BeNull();
    }

    [Fact]
    public async Task GetLatest_PicksMostRecentByTimestamp()
    {
        var store = new InMemoryCheckpointStorage();
        var older = new WorkflowCheckpoint(Guid.NewGuid().ToString(), "wf", DateTimeOffset.UtcNow.AddMinutes(-10), "{}");
        var newer = new WorkflowCheckpoint(Guid.NewGuid().ToString(), "wf", DateTimeOffset.UtcNow, "{}");
        await store.SaveAsync(older);
        await store.SaveAsync(newer);
        var latest = await store.GetLatestAsync("wf");
        latest!.CheckpointId.Should().Be(newer.CheckpointId);
    }
}

public sealed class FileCheckpointStorageTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), $"checkpoint-test-{Guid.NewGuid():N}");

    public void Dispose()
    {
        if (Directory.Exists(_dir)) Directory.Delete(_dir, recursive: true);
    }

    [Fact]
    public async Task RoundtripAndListPerWorkflow()
    {
        var store = new FileCheckpointStorage(_dir);
        var a1 = new WorkflowCheckpoint(Guid.NewGuid().ToString(), "wf-a", DateTimeOffset.UtcNow.AddMinutes(-1), "{\"n\":1}");
        var a2 = new WorkflowCheckpoint(Guid.NewGuid().ToString(), "wf-a", DateTimeOffset.UtcNow, "{\"n\":2}");
        var b1 = new WorkflowCheckpoint(Guid.NewGuid().ToString(), "wf-b", DateTimeOffset.UtcNow, "{\"n\":3}");
        await store.SaveAsync(a1);
        await store.SaveAsync(a2);
        await store.SaveAsync(b1);

        var aIds = await store.ListCheckpointIdsAsync("wf-a");
        aIds.Should().HaveCount(2);
        var bIds = await store.ListCheckpointIdsAsync("wf-b");
        bIds.Should().ContainSingle();

        var latest = await store.GetLatestAsync("wf-a");
        latest.Should().NotBeNull();
    }

    [Fact]
    public async Task DeleteRemovesFile()
    {
        var store = new FileCheckpointStorage(_dir);
        var cp = new WorkflowCheckpoint(Guid.NewGuid().ToString(), "wf", DateTimeOffset.UtcNow, "{}");
        await store.SaveAsync(cp);
        (await store.DeleteAsync(cp.CheckpointId)).Should().BeTrue();
        (await store.LoadAsync(cp.CheckpointId)).Should().BeNull();
    }
}

[Collection(nameof(LocalPostgresCollection))]
public sealed class PostgresCheckpointStorageTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private PostgresCheckpointStorage _store = null!;

    public PostgresCheckpointStorageTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);
        _store = new PostgresCheckpointStorage(_pool);
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE workflow_checkpoints");
    }

    public async Task DisposeAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync("TRUNCATE workflow_checkpoints");
        await _pool.DisposeAsync();
    }

    [Fact]
    public async Task SaveLoadDelete_Roundtrip()
    {
        var cp = new WorkflowCheckpoint(
            Guid.NewGuid().ToString(),
            "wf",
            DateTimeOffset.UtcNow,
            "{\"superstep\":1}"
        );
        await _store.SaveAsync(cp);
        var loaded = await _store.LoadAsync(cp.CheckpointId);
        loaded.Should().NotBeNull();
        loaded!.WorkflowName.Should().Be("wf");
        loaded.PayloadJson.Should().Contain("superstep");

        (await _store.DeleteAsync(cp.CheckpointId)).Should().BeTrue();
        (await _store.LoadAsync(cp.CheckpointId)).Should().BeNull();
    }

    [Fact]
    public async Task Save_UpsertsOnConflict()
    {
        var id = Guid.NewGuid().ToString();
        await _store.SaveAsync(new WorkflowCheckpoint(id, "wf", DateTimeOffset.UtcNow, "{\"v\":1}"));
        await _store.SaveAsync(new WorkflowCheckpoint(id, "wf", DateTimeOffset.UtcNow, "{\"v\":2}"));

        await using var conn = await _pool.OpenAsync();
        var count = await conn.ExecuteScalarAsync<int>(
            "SELECT COUNT(*) FROM workflow_checkpoints WHERE checkpoint_id = @id",
            new { id = Guid.Parse(id) }
        );
        count.Should().Be(1);

        var loaded = await _store.LoadAsync(id);
        loaded!.PayloadJson.Should().Contain("\"v\": 2");
    }

    [Fact]
    public async Task GetLatest_PicksMostRecent()
    {
        var older = new WorkflowCheckpoint(Guid.NewGuid().ToString(), "wf", DateTimeOffset.UtcNow.AddMinutes(-30), "{}");
        var newer = new WorkflowCheckpoint(Guid.NewGuid().ToString(), "wf", DateTimeOffset.UtcNow, "{}");
        await _store.SaveAsync(older);
        await _store.SaveAsync(newer);

        var latest = await _store.GetLatestAsync("wf");
        latest!.CheckpointId.Should().Be(newer.CheckpointId);
    }

    [Fact]
    public async Task ListCheckpointIds_ReturnsNewestFirst()
    {
        var old1 = new WorkflowCheckpoint(Guid.NewGuid().ToString(), "wf", DateTimeOffset.UtcNow.AddMinutes(-20), "{}");
        var old2 = new WorkflowCheckpoint(Guid.NewGuid().ToString(), "wf", DateTimeOffset.UtcNow.AddMinutes(-10), "{}");
        var newest = new WorkflowCheckpoint(Guid.NewGuid().ToString(), "wf", DateTimeOffset.UtcNow, "{}");
        await _store.SaveAsync(old1);
        await _store.SaveAsync(old2);
        await _store.SaveAsync(newest);

        var ids = await _store.ListCheckpointIdsAsync("wf");
        ids[0].Should().Be(newest.CheckpointId);
    }

    [Fact]
    public void FactoryHonoursBackendSetting()
    {
        CheckpointStorageFactory.Build(new AgentSettings { MafCheckpointBackend = "memory" })
            .Should().BeOfType<InMemoryCheckpointStorage>();

        var path = Path.Combine(Path.GetTempPath(), $"cf-{Guid.NewGuid():N}");
        CheckpointStorageFactory.Build(
            new AgentSettings { MafCheckpointBackend = "file", MafCheckpointDir = path }
        ).Should().BeOfType<FileCheckpointStorage>();
        Directory.Delete(path, recursive: true);

        CheckpointStorageFactory.Build(
            new AgentSettings { MafCheckpointBackend = "postgres" },
            _pool
        ).Should().BeOfType<PostgresCheckpointStorage>();

        Action missingPool = () => CheckpointStorageFactory.Build(
            new AgentSettings { MafCheckpointBackend = "postgres" }
        );
        missingPool.Should().Throw<InvalidOperationException>();
    }
}
