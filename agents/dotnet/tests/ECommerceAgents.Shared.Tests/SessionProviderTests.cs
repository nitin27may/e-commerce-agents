using Dapper;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Sessions;
using ECommerceAgents.TestFixtures;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

public sealed class InMemorySessionHistoryProviderTests
{
    [Fact]
    public async Task GetMessagesAsync_EmptyForUnknownSession()
    {
        var provider = new InMemorySessionHistoryProvider();
        var messages = await provider.GetMessagesAsync("session-a");
        messages.Should().BeEmpty();
    }

    [Fact]
    public async Task GetMessagesAsync_EmptyForNullOrEmptySessionId()
    {
        var provider = new InMemorySessionHistoryProvider();
        (await provider.GetMessagesAsync(null)).Should().BeEmpty();
        (await provider.GetMessagesAsync("")).Should().BeEmpty();
    }

    [Fact]
    public async Task SaveAndLoad_RoundtripsAcrossMultipleCalls()
    {
        var provider = new InMemorySessionHistoryProvider();
        await provider.SaveMessagesAsync("s", [new StoredMessage("user", "hi")]);
        await provider.SaveMessagesAsync("s", [new StoredMessage("assistant", "hello")]);

        var messages = await provider.GetMessagesAsync("s");
        messages.Should().HaveCount(2);
        messages[0].Role.Should().Be("user");
        messages[1].Content.Should().Be("hello");
    }

    [Fact]
    public async Task SaveAsync_NoOpOnEmptyMessages()
    {
        var provider = new InMemorySessionHistoryProvider();
        await provider.SaveMessagesAsync("s", Array.Empty<StoredMessage>());
        (await provider.GetMessagesAsync("s")).Should().BeEmpty();
    }
}

public sealed class FileSessionHistoryProviderTests : IDisposable
{
    private readonly string _dir = Path.Combine(
        Path.GetTempPath(),
        $"session-test-{Guid.NewGuid():N}"
    );

    public void Dispose()
    {
        if (Directory.Exists(_dir)) Directory.Delete(_dir, recursive: true);
    }

    [Fact]
    public async Task RoundtripsThroughJsonl()
    {
        var provider = new FileSessionHistoryProvider(_dir);
        await provider.SaveMessagesAsync("abc", [
            new StoredMessage("user", "hi"),
            new StoredMessage("assistant", "hello"),
        ]);

        var loaded = await provider.GetMessagesAsync("abc");
        loaded.Should().HaveCount(2);

        // Second save appends.
        await provider.SaveMessagesAsync("abc", [new StoredMessage("user", "next")]);
        var again = await provider.GetMessagesAsync("abc");
        again.Should().HaveCount(3);
    }

    [Fact]
    public async Task SanitisesPathSeparatorsInSessionId()
    {
        var provider = new FileSessionHistoryProvider(_dir);
        await provider.SaveMessagesAsync("a/b/c", [new StoredMessage("user", "x")]);
        var loaded = await provider.GetMessagesAsync("a/b/c");
        loaded.Should().HaveCount(1);
        File.Exists(Path.Combine(_dir, "a_b_c.jsonl")).Should().BeTrue();
    }

    [Fact]
    public async Task EmptyForUnknownSession()
    {
        var provider = new FileSessionHistoryProvider(_dir);
        (await provider.GetMessagesAsync("ghost")).Should().BeEmpty();
    }
}

[Collection(nameof(LocalPostgresCollection))]
public sealed class PostgresSessionHistoryProviderTests : IAsyncLifetime
{
    private readonly PostgresFixture _pg;
    private DatabasePool _pool = null!;
    private PostgresSessionHistoryProvider _provider = null!;
    private Guid _userId;
    private Guid _conversationId;

    public PostgresSessionHistoryProviderTests(PostgresFixture pg) => _pg = pg;

    public async Task InitializeAsync()
    {
        var settings = new AgentSettings { DatabaseUrl = _pg.ConnectionString };
        _pool = new DatabasePool(settings);
        _provider = new PostgresSessionHistoryProvider(_pool);
        await SeedAsync();
    }

    public async Task DisposeAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE messages, conversations, users RESTART IDENTITY CASCADE"
        );
        await _pool.DisposeAsync();
    }

    [Fact]
    public async Task RoundtripsMessagesThroughDb()
    {
        await _provider.SaveMessagesAsync(_conversationId.ToString(), [
            new StoredMessage("user", "hi"),
            new StoredMessage("assistant", "hello"),
        ]);

        var messages = await _provider.GetMessagesAsync(_conversationId.ToString());
        messages.Should().HaveCount(2);
        messages[0].Content.Should().Be("hi");
    }

    [Fact]
    public async Task SkipsEmptyContent()
    {
        await _provider.SaveMessagesAsync(_conversationId.ToString(), [
            new StoredMessage("user", ""),
            new StoredMessage("user", "kept"),
        ]);
        var messages = await _provider.GetMessagesAsync(_conversationId.ToString());
        messages.Should().HaveCount(1);
        messages[0].Content.Should().Be("kept");
    }

    [Fact]
    public async Task IgnoresNonUuidSessionId()
    {
        var msgs = await _provider.GetMessagesAsync("not-a-uuid");
        msgs.Should().BeEmpty();
    }

    [Fact]
    public void FactoryHonoursSettings()
    {
        var memProvider = SessionProviderFactory.Build(new AgentSettings { MafSessionBackend = "memory" });
        memProvider.Should().BeOfType<InMemorySessionHistoryProvider>();

        var filePath = Path.Combine(Path.GetTempPath(), $"session-factory-{Guid.NewGuid():N}");
        var fileProvider = SessionProviderFactory.Build(
            new AgentSettings { MafSessionBackend = "file", MafSessionDir = filePath }
        );
        fileProvider.Should().BeOfType<FileSessionHistoryProvider>();
        Directory.Delete(filePath, recursive: true);

        var pgProvider = SessionProviderFactory.Build(
            new AgentSettings { MafSessionBackend = "postgres" },
            _pool
        );
        pgProvider.Should().BeOfType<PostgresSessionHistoryProvider>();

        Action missingPool = () => SessionProviderFactory.Build(
            new AgentSettings { MafSessionBackend = "postgres" }
        );
        missingPool.Should().Throw<InvalidOperationException>();
    }

    private async Task SeedAsync()
    {
        await using var conn = await _pool.OpenAsync();
        await conn.ExecuteAsync(
            "TRUNCATE messages, conversations, users RESTART IDENTITY CASCADE"
        );

        _userId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO users (email, password_hash, name, role)
              VALUES ('session@example.com', 'x', 'Tester', 'customer')
              RETURNING id"
        );
        _conversationId = await conn.ExecuteScalarAsync<Guid>(
            @"INSERT INTO conversations (user_id, title)
              VALUES (@uid, 'Test') RETURNING id",
            new { uid = _userId }
        );
    }
}
