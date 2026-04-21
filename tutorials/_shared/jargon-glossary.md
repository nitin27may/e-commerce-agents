# MAF Jargon Glossary

Short, working definitions for the technical terms used across the series. Each chapter README should define jargon **inline the first time it appears**, with a one-line anchor that matches what's written here. Update this file whenever a term is introduced or retired.

## Framework primitives

**Microsoft Agent Framework (MAF)** — Microsoft's SDK for building LLM-powered agents and multi-agent workflows in Python (`agent-framework`) and .NET (`Microsoft.Agents.AI`). Merges the opinions of AutoGen (multi-agent) and Semantic Kernel (enterprise integrations) into a single abstraction.

**Agent** — An LLM plus instructions, tools, optional session, and middleware. In MAF, every agent ultimately wraps an `IChatClient` (.NET) / `ChatClient` (Python). See `ChatClientAgent`.

**ChatClientAgent** (.NET) / `ChatAgent` (Python) — The default agent implementation. Takes a chat client, instructions, name, description, and a list of tools. Use `.AsAIAgent()` on a provider client to get one.

**AIAgent** — The abstract base class in .NET that all agents inherit from. The polymorphic type you pass around in generic code.

**AIProjectClient** — Entry point for Microsoft Foundry in .NET. Use `.AsAIAgent(model:, instructions:)` to turn it into an agent without creating a server-side Foundry agent record.

**Instructions** — The system prompt. In MAF, this is a first-class field on the agent, not a message you prepend.

**IChatClient** — The .NET abstraction every provider implements. Swapping providers means swapping the chat client, not the agent.

## Tools

**Tool** — A function (or hosted capability like code interpreter) the agent can call. Defined in user code; executed by the framework; results fed back to the LLM.

**@tool decorator** (Python) — Marks a Python function as a tool. MAF builds the JSON schema from `Annotated[type, Field(description=...)]` annotations.

**[Description] attribute** (.NET) — Decorates a method and its parameters so `AIFunctionFactory.Create()` can build the JSON schema MAF advertises to the LLM.

**AIFunctionFactory.Create** (.NET) — Turns a regular method into an `AIFunction` the agent can accept in its `tools:` list.

**Tool-calling loop** — The cycle where the LLM emits a structured token that names a tool and its arguments; the framework parses it, invokes the function, feeds the result back into the conversation, and asks the LLM for a final response. MAF runs this loop for you.

**MCP (Model Context Protocol)** — A JSON-RPC protocol for advertising tools over stdio, HTTP, or SSE. Lets agents use tools implemented in other processes or languages.

**Tool approval** — A middleware pattern where a human must confirm a tool call before the framework executes it. Implemented via function middleware + a gating mechanism.

## Sessions and memory

**AgentSession** — The object that carries conversation state across `RunAsync`/`run()` calls. In .NET it's a class with a `StateBag`; in Python it's a value object with `session_id`, optional `service_session_id`, and a `state` dict.

**ChatHistoryProvider** — Stores conversation messages. `InMemoryChatHistoryProvider` is the default. Swap for Redis, Cosmos DB, or a custom backend.

**Context provider** — A middleware-like object that runs before every agent turn to inject extra context (user profile, memories, retrieved documents). Sees the conversation, returns an `AIContext` with extended instructions, messages, or tools.

**AIContextProviders** — Collection of context providers registered on an agent. Executed in order before each LLM call.

**TextSearchProvider** — The RAG context provider. Wraps a search function and injects matching documents into context before the LLM runs.

**RAG (Retrieval Augmented Generation)** — Fetching relevant documents at runtime and giving them to the LLM as context, instead of relying on training data alone. Implemented in MAF as a context provider.

**Memory** — Long-lived facts about a user or domain, recalled across sessions. Stored in your datastore, surfaced via a context provider.

## Middleware

**Middleware** — Code that runs around an agent call. MAF has three layers: agent-run, function-calling, and chat-client.

**AgentMiddleware / Agent Run Middleware** — Wraps the entire agent turn. Use for logging, tracing, rate limiting, high-level short-circuits.

**FunctionMiddleware / Function Calling Middleware** — Wraps each individual tool call. Use for approval gates, per-tool logging, auditing, validation.

**ChatMiddleware / IChatClient Middleware** — Wraps the raw LLM call. Use for PII redaction, request/response transformation, content filtering.

**DelegatingChatClient** (.NET) — The base class for implementing chat middleware. Override `GetResponseAsync` and `GetStreamingResponseAsync` and call `base.<method>` to pass through.

**Short-circuit / MiddlewareTermination** — A middleware that sets a result and returns (or raises in Python) to skip downstream middleware and the LLM call.

**.AsBuilder().Use(...)** (.NET) — The fluent API for attaching middleware. Returns a **new** decorated agent; does not mutate the original.

## Observability

**OpenTelemetry (OTel)** — The standard for distributed tracing, metrics, and logs. MAF emits OTel spans out of the box.

**Span** — A named unit of work with a start time, end time, and attributes. `invoke_agent`, `chat`, `execute_tool` are the three main agent spans.

**TracerProvider** — The OTel object that creates tracers. You build one per process and configure exporters.

**Exporter** — Where spans go (Console, OTLP over gRPC/HTTP, Azure Monitor, Aspire Dashboard).

**GenAI semantic attributes** — Standard attribute names for AI spans (`gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, etc.). MAF applies them automatically.

**Aspire Dashboard** — .NET's dev-time telemetry UI. Shows traces, metrics, structured logs from all services in the compose stack. Runs at `:18888` in this repo.

**DevUI** — The MAF-native browser dashboard for interactively testing a single agent or workflow. Separate from Aspire: Aspire visualises production telemetry; DevUI is an interactive test harness.

## Workflows

**Workflow** — A directed graph of executors connected by edges. Deterministic orchestration, type-safe routing, checkpointable. Contrast with an agent, which is LLM-driven.

**Executor** — A node in a workflow that processes a typed message and either routes to downstream executors or yields an output.

**Edge** — A connection between two executors. Can be conditional (predicate-gated).

**WorkflowBuilder** — The fluent API for assembling a workflow.

**WorkflowContext** — Passed into each executor handler. Exposes `send_message`, `yield_output`, and `emit`.

**Superstep** — One round of the workflow scheduler. All pending messages are dispatched concurrently; the next superstep fires after every executor in this one finishes. Based on the Pregel model.

**[MessageHandler] attribute** (.NET) — Marks an executor method as a handler for a specific input type. A source generator wires these up at build time.

**@handler decorator** (Python) — Same role as `[MessageHandler]`, but at runtime.

**WorkflowEvent** — An event emitted during execution. Has a type (`executor_invoked`, `executor_completed`, `output`, custom) and a data payload. Used for observability and for wiring UIs.

**AgentExecutor** — A built-in executor that runs an `AIAgent` as a workflow step. Takes `AgentExecutorRequest`, produces `AgentExecutorResponse`.

**InputAdapter / OutputAdapter** — Small executors that convert between domain types and `AgentExecutorRequest` / `AgentExecutorResponse`. Hidden by convenience builders (`SequentialBuilder`, `ConcurrentBuilder`).

## Orchestration patterns

**SequentialBuilder** — Builds a workflow that runs agents in order. Each agent sees prior turns.

**ConcurrentBuilder** — Builds a workflow that runs agents in parallel. Combine outputs with an aggregator.

**HandoffBuilder** — Builds a mesh of agents that can hand off control to each other. Agents emit synthesized `handoff_to_<name>` tool calls. Cycles prevented by `turn_limits`.

**GroupChatBuilder** — Builds a multi-turn discussion. A manager (`RoundRobinGroupChatManager`, `PromptDrivenGroupChatManager`, or custom) picks the next speaker each round.

**Magentic / StandardMagenticManager** — An LLM-driven orchestration pattern where the manager maintains a **facts ledger** (things it learned) and a **plan** (next steps), delegates to workers, observes results, and iterates until satisfied.

**Facts ledger** — The running list of information the Magentic manager has collected about the task.

**Plan** — The Magentic manager's current outline of remaining steps.

**Turn limits** — A budget on how many times an agent can hand off or a manager can iterate. Prevents runaway loops.

**Aggregator** — A function that reduces the outputs of several parallel agents into a single result.

## HITL and checkpointing

**Human-in-the-loop (HITL)** — A workflow pattern where execution pauses to ask a human a question, then resumes when the human answers.

**request_info** — The event type emitted when a workflow pauses for human input. Carries a `request_id` the caller must pair with the user's response.

**@response_handler (Python) / [ResponseHandler] (.NET)** — Marks the method that resumes the workflow once the human response arrives.

**Checkpoint** — A serialized snapshot of workflow state taken at superstep boundaries. Used to resume after a crash or across a pause.

**CheckpointStorage** — The interface for saving and loading checkpoints. Implementations: `InMemory`, `File`, `Postgres`, `Cosmos`.

**on_checkpoint_save / on_checkpoint_restore** — Executor hooks for customising what gets serialised and how it's restored.

## Declarative and visualization

**Declarative workflow** — A workflow defined in YAML rather than code. Loaded at runtime by a `WorkflowFactory` that parses the spec, instantiates executors, and wires edges.

**Op registry** — The mapping from string identifiers in the YAML (`"upper"`, `"non_empty"`, `"prefix"`) to executor implementations.

**Visualization** — Rendering a workflow graph to Mermaid or Graphviz DOT. Deterministic output so diffs in PRs are meaningful.

## Auth and request context

**A2A (Agent-to-Agent)** — HTTP protocol for inter-agent communication in this repo. Uses `/message:send` endpoints, `x-agent-secret` header, forwarded user identity headers.

**ContextVar** — Python `contextvars.ContextVar` — async-safe per-request state. Used in the capstone for `current_user_email`, `current_session_id` instead of threading parameters through every function.

**JWT** — The user access token carried in the `Authorization: Bearer …` header. Validated by `AgentAuthMiddleware`.

---

**Maintenance:** when you introduce a new term in a chapter, add it here in the same wording. When a term falls out of use, mark it `(retired)` rather than deleting.
