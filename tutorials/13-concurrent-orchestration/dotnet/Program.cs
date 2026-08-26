// MAF v1 — Chapter 13: Concurrent Orchestration (.NET)
//
// Three agents review the same product idea in parallel. Researcher flags
// market fit, Marketer proposes positioning, Legal raises one regulatory
// concern. AgentWorkflowBuilder.BuildConcurrent fans the input out to all
// three; a custom aggregator fans their outputs back in as one synthesised
// summary message.
//
// Run:
//   cd tutorials/13-concurrent-orchestration/dotnet
//   dotnet run                              # default idea
//   dotnet run -- "ultrasonic pet collar"   # custom idea
//
// Requires OPENAI_API_KEY (or Azure OpenAI env vars) in repo-root .env.

using System.ClientModel;
using System.Diagnostics;
using System.Text;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Extensions.AI;
using OpenAI;
using OpenAI.Chat;

using ChatMessage = Microsoft.Extensions.AI.ChatMessage;

namespace MafV1.Ch13.Concurrent;

/// <summary>
/// The two halves of a concurrent run: what each agent said on its own, and
/// what the fan-in aggregator made of the set.
/// </summary>
/// <param name="PerAgent">One message per agent, in completion order.</param>
/// <param name="Summary">The aggregator's synthesised text.</param>
public sealed record ConcurrentReview(IReadOnlyList<ChatMessage> PerAgent, string Summary);

public static class Program
{
    internal const string ResearcherInstructions =
        "You are a Market Researcher. In ONE sentence, assess the market fit of the product idea the user provides.";

    internal const string MarketerInstructions =
        "You are a Marketer. In ONE sentence, propose a positioning angle for the product idea the user provides.";

    internal const string LegalInstructions =
        "You are a Legal advisor. In ONE sentence, flag ONE regulatory or IP concern about the product idea.";

    public static async Task<int> Main(string[] args)
    {
        LoadDotEnv();

        string idea = args.Length > 0 ? args[0] : "a subscription box for rare herbal teas";
        Console.WriteLine($"Idea: {idea}");
        Console.WriteLine();

        var stopwatch = Stopwatch.StartNew();
        ConcurrentReview review = await RunAsync(BuildChatClient().AsIChatClient(), idea);
        stopwatch.Stop();

        foreach (ChatMessage message in review.PerAgent)
        {
            Console.WriteLine($"[{message.AuthorName ?? "agent"}] {message.Text.Trim()}");
            Console.WriteLine();
        }

        Console.WriteLine("===== Aggregated summary =====");
        Console.WriteLine(review.Summary);
        Console.WriteLine();
        Console.WriteLine($"Wall-clock: {stopwatch.Elapsed.TotalSeconds:F2}s (three LLM calls ran in parallel)");

        return 0;
    }

    /// <summary>
    /// Builds the three-agent fan-out with <see cref="SynthesizeReview"/> as
    /// the fan-in. Takes an <see cref="IChatClient"/> so the test project can
    /// drive the whole thing with a scripted client — no key, no network.
    /// </summary>
    public static Workflow BuildWorkflow(IChatClient chatClient)
    {
        AIAgent researcher = chatClient.AsAIAgent(instructions: ResearcherInstructions, name: "researcher");
        AIAgent marketer = chatClient.AsAIAgent(instructions: MarketerInstructions, name: "marketer");
        AIAgent legal = chatClient.AsAIAgent(instructions: LegalInstructions, name: "legal");

        // BuildConcurrent takes an optional aggregator:
        //   Func<IList<List<ChatMessage>>, List<ChatMessage>>
        // Each outer-list entry is one agent's emitted messages, in the
        // same order as the agents were passed in. Our aggregator reduces
        // three message lists into one synthesised summary message.
        return AgentWorkflowBuilder.BuildConcurrent(
            new[] { researcher, marketer, legal },
            aggregator: SynthesizeReview);
    }

    /// <summary>
    /// Runs the fan-out/fan-in and returns both halves: each agent's own
    /// verdict, and the aggregator's synthesis.
    /// </summary>
    /// <remarks>
    /// Read the results off the terminal <see cref="WorkflowOutputEvent"/>,
    /// not <c>AgentResponseEvent</c>. The agent-wrapping executors emit
    /// <c>AgentResponseUpdateEvent</c> as they stream; there is no
    /// <c>AgentResponseEvent</c> on this path in Microsoft.Agents.AI.Workflows
    /// 1.1.0, so matching on it compiles and silently yields nothing.
    /// </remarks>
    public static async Task<ConcurrentReview> RunAsync(IChatClient chatClient, string idea)
    {
        Workflow workflow = BuildWorkflow(chatClient);

        var messages = new List<ChatMessage> { new(ChatRole.User, idea) };

        await using StreamingRun run = await InProcessExecution.RunStreamingAsync(workflow, messages);

        // The wrapped agents are lazy: without a TurnToken they cache their
        // input and never call the model.
        await run.TrySendMessageAsync(new TurnToken(emitEvents: true));

        var perAgent = new List<ChatMessage>();
        List<ChatMessage>? aggregated = null;

        await foreach (WorkflowEvent evt in run.WatchStreamAsync())
        {
            switch (evt)
            {
                case AgentResponseUpdateEvent update when update.Data is AgentResponseUpdate chunk:
                    // One update per agent per chunk. The scripted-client case
                    // yields exactly one chunk each; a real provider yields many,
                    // so accumulate by author rather than assuming one.
                    ChatMessage? existing = perAgent.FirstOrDefault(m => m.AuthorName == chunk.AuthorName);
                    if (existing is null)
                    {
                        perAgent.Add(new ChatMessage(ChatRole.Assistant, chunk.Text) { AuthorName = chunk.AuthorName });
                    }
                    else
                    {
                        existing.Contents.Add(new TextContent(chunk.Text));
                    }
                    break;

                case WorkflowOutputEvent output when output.Data is List<ChatMessage> list:
                    // The aggregator's return value surfaces as the
                    // workflow's terminal output — see SynthesizeReview.
                    aggregated = list;
                    break;
            }
        }

        string summary = aggregated is null
            ? string.Empty
            : string.Join("\n", aggregated.Select(m => m.Text.Trim()));

        return new ConcurrentReview(perAgent, summary);
    }

    /// <summary>
    /// Fan-in aggregator. Receives one <see cref="ChatMessage"/> list per
    /// concurrent agent — same order as the agents were passed in — and
    /// returns a single list representing the workflow's terminal output.
    /// </summary>
    /// <remarks>
    /// This runs after every concurrent branch has completed. No LLM call;
    /// the function is deterministic so the wall-clock stays dominated by
    /// the slowest agent. If you want a synthesising LLM summary instead,
    /// call an agent inside this function — the signature is an async
    /// boundary, so it's safe to await.
    /// </remarks>
    internal static List<ChatMessage> SynthesizeReview(IList<List<ChatMessage>> perAgentMessages)
    {
        var builder = new StringBuilder();
        builder.AppendLine("Cross-functional review:");

        foreach (List<ChatMessage> agentOutput in perAgentMessages)
        {
            if (agentOutput.Count == 0)
            {
                continue;
            }

            // The last assistant message per agent is that agent's verdict;
            // earlier messages (if any) are tool calls or scratch turns.
            ChatMessage final = agentOutput[^1];
            string label = final.AuthorName ?? "agent";
            builder.Append("- ").Append(label).Append(": ").AppendLine(final.Text.Trim());
        }

        return new List<ChatMessage>
        {
            new(ChatRole.Assistant, builder.ToString().TrimEnd())
            {
                AuthorName = "concurrent-aggregator",
            },
        };
    }

    private static ChatClient BuildChatClient()
    {
        string provider = Environment.GetEnvironmentVariable("LLM_PROVIDER")?.ToLowerInvariant() ?? "openai";

        if (provider == "azure")
        {
            return new AzureOpenAIClient(
                    new Uri(Required("AZURE_OPENAI_ENDPOINT")),
                    new ApiKeyCredential(
                        Environment.GetEnvironmentVariable("AZURE_OPENAI_KEY")
                        ?? Required("AZURE_OPENAI_API_KEY")))
                .GetChatClient(Required("AZURE_OPENAI_DEPLOYMENT"));
        }

        return new OpenAIClient(new ApiKeyCredential(Required("OPENAI_API_KEY")))
            .GetChatClient(Environment.GetEnvironmentVariable("LLM_MODEL") ?? "gpt-4.1");
    }

    private static string Required(string name) =>
        Environment.GetEnvironmentVariable(name)
            ?? throw new InvalidOperationException($"{name} must be set (see repo-root .env).");

    private static void LoadDotEnv()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, ".env")))
        {
            dir = dir.Parent;
        }
        if (dir is null)
        {
            return;
        }

        foreach (string raw in File.ReadAllLines(Path.Combine(dir.FullName, ".env")))
        {
            string line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#'))
            {
                continue;
            }
            int eq = line.IndexOf('=');
            if (eq < 0)
            {
                continue;
            }
            string key = line[..eq].Trim();
            string value = line[(eq + 1)..].Trim().Trim('"').Trim('\'');
            if (Environment.GetEnvironmentVariable(key) is null)
            {
                Environment.SetEnvironmentVariable(key, value);
            }
        }
    }
}
