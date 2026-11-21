using System.Text.Json;
using ECommerceAgents.Shared.Prompts;
using FluentAssertions;
using Xunit;

namespace ECommerceAgents.Shared.Tests;

/// <summary>
/// The group-chat panelist and eval-judge prompts live in shared YAML read raw by both stacks. The
/// text must equal the pre-move literal (the snapshot Python's tests also pin), or replay fixtures miss.
/// </summary>
public sealed class RawPromptTests
{
    private static string RepoPython()
    {
        var dir = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "agents", "python", "config", "prompts")))
        {
            dir = dir.Parent;
        }
        dir.Should().NotBeNull();
        return Path.Combine(dir!.FullName, "agents", "python");
    }

    public static TheoryData<string> Names() => new()
    {
        "group-chat-value",
        "group-chat-quality",
        "_eval/completeness-judge",
    };

    [Theory]
    [MemberData(nameof(Names))]
    public void LoadRaw_MatchesTheSnapshotOfTheOldLiteral(string name)
    {
        var root = RepoPython();
        var snapshots = JsonSerializer.Deserialize<Dictionary<string, string>>(
            File.ReadAllText(Path.Combine(root, "tests", "data", "group_chat_prompt_snapshots.json")))!;

        new PromptLoader(Path.Combine(root, "config", "prompts")).LoadRaw(name).Should().Be(snapshots[name]);
    }

    [Fact]
    public void LoadRaw_ThrowsForAMissingFile()
    {
        var act = () => new PromptLoader(Path.Combine(RepoPython(), "config", "prompts")).LoadRaw("nope");

        act.Should().Throw<FileNotFoundException>();
    }
}
