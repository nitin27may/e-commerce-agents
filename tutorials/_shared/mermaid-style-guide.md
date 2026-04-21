# Mermaid Diagram Style Guide

All chapter diagrams follow this guide so the series reads consistently and every diagram renders cleanly in both light and dark Hugo themes.

## Why this exists

Mermaid's default theme is tuned for light backgrounds. It produces washed-out nodes on dark pages and illegible arrow labels. This guide pins a palette with enough contrast in both modes (WCAG AA, text ≥ 4.5:1 on its fill) and a small set of semantic classes you can reuse.

## The palette

| Class      | Role                                    | Fill        | Stroke      | Text     |
|------------|-----------------------------------------|-------------|-------------|----------|
| `core`     | Core services, agents, MAF primitives   | `#2563eb`   | `#1e40af`   | `#ffffff`|
| `external` | External APIs, LLMs, MCP servers        | `#f59e0b`   | `#b45309`   | `#000000`|
| `success`  | Validated output, success paths         | `#10b981`   | `#047857`   | `#ffffff`|
| `error`    | Error paths, security boundaries        | `#ef4444`   | `#b91c1c`   | `#ffffff`|
| `infra`    | Databases, caches, infra, supporting    | `#64748b`   | `#334155`   | `#ffffff`|

No other colours. No gradients. No emoji in node labels.

## Boilerplate — copy this at the top of every diagram

```
%%{init: {'theme':'base', 'themeVariables': {
  'primaryColor': '#2563eb',
  'primaryTextColor': '#ffffff',
  'primaryBorderColor': '#1e40af',
  'lineColor': '#64748b',
  'secondaryColor': '#f59e0b',
  'tertiaryColor': '#10b981',
  'background': 'transparent'
}}}%%
flowchart LR
  classDef core     fill:#2563eb,stroke:#1e40af,color:#ffffff
  classDef external fill:#f59e0b,stroke:#b45309,color:#000000
  classDef success  fill:#10b981,stroke:#047857,color:#ffffff
  classDef error    fill:#ef4444,stroke:#b91c1c,color:#ffffff
  classDef infra    fill:#64748b,stroke:#334155,color:#ffffff
```

Then assign classes to nodes with `class` statements:

```
class userAgent core
class openai external
class postgres infra
```

## Example — a tool-calling loop (Ch02)

```
%%{init: {'theme':'base', 'themeVariables': {
  'primaryColor': '#2563eb','primaryTextColor': '#ffffff','primaryBorderColor': '#1e40af',
  'lineColor': '#64748b','secondaryColor': '#f59e0b','tertiaryColor': '#10b981',
  'background': 'transparent'}}}%%
flowchart LR
  classDef core     fill:#2563eb,stroke:#1e40af,color:#ffffff
  classDef external fill:#f59e0b,stroke:#b45309,color:#000000
  classDef success  fill:#10b981,stroke:#047857,color:#ffffff

  user([User question])
  agent[Agent]
  llm[(LLM)]
  tool[[get_weather tool]]
  answer([Final answer])

  user --> agent
  agent -- "prompt + tool schemas" --> llm
  llm -- "decides to call tool" --> agent
  agent -- "invokes function" --> tool
  tool -- "result" --> agent
  agent -- "result in context" --> llm
  llm -- "final text" --> agent
  agent --> answer

  class agent core
  class llm external
  class tool core
  class answer success
```

Nodes use shape to reinforce meaning: `([rounded])` for user-facing, `[rect]` for services/agents, `[(cylinder)]` for datastores/LLMs, `[[hexagon]]` for tools/functions.

## Supported diagram types

| Diagram       | Use when                                                  |
|---------------|-----------------------------------------------------------|
| `flowchart`   | Component relationships, data flow, pipelines             |
| `sequenceDiagram` | Time-ordered message exchanges (A2A, HITL, streaming) |
| `stateDiagram-v2` | Lifecycles (sessions, checkpoints, Magentic manager)  |
| `classDiagram` | Rarely — only if inheritance / composition is the point  |

Avoid `gantt`, `pie`, `journey`, `quadrantChart` — they don't respect the palette.

## Rules

1. **Every chapter gets at least one diagram.** Placed in "The concept" section, before any code.
2. **Copy the init block unchanged.** Don't tune colours per chapter.
3. **Assign classes to every node.** Unclassed nodes fall back to Mermaid defaults and look different in dark mode.
4. **Keep node labels short.** Under 40 chars. Use edge labels for verbs.
5. **No emoji in labels** (per project convention).
6. **Prefer horizontal (`LR`) over vertical (`TD`)** unless the concept is genuinely hierarchical.
7. **Wrap long flows across 2 rows** using subgraphs rather than one giant DAG.
8. **Link captions under diagrams.** One sentence naming what the diagram proves: *"The LLM never executes the function — it asks the framework to, then sees the result in its next context window."*

## Verification

Before committing a diagram:

1. Preview in Hugo (`hugo server`) and toggle theme — every node must stay readable.
2. Run the Mermaid CLI if available: `npx -y @mermaid-js/mermaid-cli -i diagram.mmd -o /tmp/d.svg` — errors fail the build.
3. Keep the diagram under ~25 nodes. Anything larger is two diagrams.
