// MAF v1 — Chapter 15: Group Chat Orchestration (.NET)
//
// Reference: AgentWorkflowBuilder.CreateGroupChatBuilderWith(managerFactory)
// creates a centralized-manager group chat. Managers can be round-robin,
// prompt-driven, or fully custom (an Agent itself). Full runnable lives in
// the capstone follow-ups; Python is the canonical working example here.

namespace MafV1.Ch15.GroupChat;

public static class Program
{
    public static void Main()
    {
        Console.WriteLine("Chapter 15 — Group Chat Orchestration");
        Console.WriteLine();
        Console.WriteLine("Python (runnable): python tutorials/15-group-chat-orchestration/python/main.py");
        Console.WriteLine();
        Console.WriteLine(".NET API surface (reference):");
        Console.WriteLine();
        Console.WriteLine("  var manager = () => new RoundRobinGroupChatManager();");
        Console.WriteLine("  // or: new PromptDrivenGroupChatManager(chatClient, systemPrompt)");
        Console.WriteLine();
        Console.WriteLine("  var workflow = AgentWorkflowBuilder");
        Console.WriteLine("      .CreateGroupChatBuilderWith(manager)");
        Console.WriteLine("      .WithParticipants(writer, critic, editor)");
        Console.WriteLine("      .WithMaxRounds(3)");
        Console.WriteLine("      .Build();");
        Console.WriteLine();
        Console.WriteLine("  await foreach (var evt in InProcessExecution.StreamAsync(workflow, topic))");
        Console.WriteLine("      if (evt is GroupChatEvent g) Console.WriteLine($\"{g.Speaker}: {g.Message}\");");
    }
}
