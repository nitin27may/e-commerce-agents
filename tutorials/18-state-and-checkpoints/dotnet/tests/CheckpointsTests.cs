// MAF v1 — Chapter 18 tests (State and Checkpoints)
//
// The claim under test is the one that is hardest to believe by reading code
// and easiest to break by accident: executor state survives a round trip
// through JSON on disk, into a process that never saw the original run.
//
// What makes this worth a test rather than an eyeball is the failure mode.
// If OnCheckpointRestoredAsync silently does nothing, the resumed run still
// completes, still yields a number, and still prints something that looks like
// a refund — just the wrong one. There is no exception, no warning, and the
// two numbers are close enough that nobody notices in a demo.
//
// No LLM, no network. Each test gets its own temp directory so the file-backed
// store cannot leak state between them.

using FluentAssertions;
using Xunit;

namespace MafV1.Ch18.Checkpoints.Tests;

public sealed class CheckpointsTests : IDisposable
{
    private readonly DirectoryInfo _dir;

    public CheckpointsTests()
    {
        _dir = new DirectoryInfo(Path.Combine(Path.GetTempPath(), "ch18-" + Guid.NewGuid().ToString("N")));
        _dir.Create();
    }

    public void Dispose()
    {
        if (_dir.Exists) _dir.Delete(recursive: true);
    }

    [Fact]
    public async Task The_Run_Sums_The_Initial_And_Item_Refunds()
    {
        CheckpointRoundTrip result = await Program.RunAsync(_dir, initialRefund: 10.0, itemRefund: 5.0);

        result.FirstRun.Should().Be(15.0);
    }

    [Fact]
    public async Task Resuming_From_A_Checkpoint_Reproduces_The_Original_Result()
    {
        // The chapter's headline claim.
        CheckpointRoundTrip result = await Program.RunAsync(_dir, initialRefund: 10.0, itemRefund: 5.0);

        result.Resumed.Should().Be(result.FirstRun);
    }

    [Fact]
    public async Task The_Restored_Total_Is_Not_Merely_The_Constructor_Seed()
    {
        // The test that actually proves state round-tripped. The resumed run
        // builds a fresh executor seeded with initialRefund, so a no-op
        // OnCheckpointRestoredAsync would produce exactly 10.0 — a plausible
        // number that passes any assertion looser than this one.
        CheckpointRoundTrip result = await Program.RunAsync(_dir, initialRefund: 10.0, itemRefund: 5.0);

        result.Resumed.Should().Be(15.0);
        result.Resumed.Should().NotBe(10.0, "10.0 is what you get when the checkpoint is ignored");
    }

    [Fact]
    public async Task Checkpoints_Are_Actually_Written_To_Disk()
    {
        CheckpointRoundTrip result = await Program.RunAsync(_dir, initialRefund: 10.0, itemRefund: 5.0);

        result.CheckpointIds.Should().NotBeEmpty();
        _dir.EnumerateFiles("*", SearchOption.AllDirectories).Should().NotBeEmpty();
    }

    [Fact]
    public async Task A_Second_Run_In_Its_Own_Directory_Is_Independent()
    {
        // Two runs must not resume from each other's checkpoints. Separate
        // directories, because of the exclusive-lock gotcha pinned below.
        CheckpointRoundTrip first = await Program.RunAsync(_dir, initialRefund: 10.0, itemRefund: 5.0);

        var other = new DirectoryInfo(Path.Combine(Path.GetTempPath(), "ch18-" + Guid.NewGuid().ToString("N")));
        other.Create();
        try
        {
            CheckpointRoundTrip second = await Program.RunAsync(other, initialRefund: 100.0, itemRefund: 1.0);

            second.FirstRun.Should().Be(101.0);
            second.Resumed.Should().Be(101.0);
            second.CheckpointIds.Should().NotIntersectWith(first.CheckpointIds);
        }
        finally
        {
            other.Delete(recursive: true);
        }
    }

    [Fact]
    public async Task A_Checkpoint_Directory_Can_Only_Back_One_Store_Per_Process()
    {
        // Pins a real constraint that reads like a bug when you hit it.
        // FileSystemJsonCheckpointStore locks its directory exclusively and
        // does not release the lock when the run ends, so a second store over
        // the same directory throws — from the SAME process, after the first
        // run completed. The message says "another process", which sends
        // people hunting for a stale lockfile that is not there.
        //
        // If a future release makes the store releasable, this test fails and
        // the warning in Program.cs should come out.
        await Program.RunAsync(_dir, initialRefund: 10.0, itemRefund: 5.0);

        var act = async () => await Program.RunAsync(_dir, initialRefund: 1.0, itemRefund: 1.0);

        await act.Should().ThrowAsync<InvalidOperationException>()
            .WithMessage("*already in use*");
    }

    [Theory]
    [InlineData(0.0, 0.0, 0.0)]
    [InlineData(10.0, 5.0, 15.0)]
    [InlineData(19.99, 0.01, 20.0)]
    public async Task State_Round_Trips_For_Any_Amounts(double initial, double item, double expected)
    {
        // Includes a case whose sum crosses a decimal boundary — the shape of
        // value most likely to expose a serialisation round-trip that is not
        // exact.
        CheckpointRoundTrip result = await Program.RunAsync(_dir, initial, item);

        result.FirstRun.Should().Be(expected);
        result.Resumed.Should().Be(expected);
    }

    [Fact]
    public void The_Workflow_Builds_Standalone()
    {
        Program.BuildWorkflow(10.0).Should().NotBeNull();
    }
}
