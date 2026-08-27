# Reported vs actual

Every entry below is a bug that turned out to be **bigger than the report said**, and in
every case the difference was found by *running* something rather than reading it.

This page exists because it is the most useful artifact this project produces and it has
been invisible — it lived in `.claude/plans/remaining-work.md`, linked once, from the
roadmap. It is kept because the pattern has not stopped repeating.

## The table

| Filed as | Actually |
|---|---|
| "follow-ups *occasionally* lose context" ([#9](https://github.com/nitin27may/e-commerce-agents/issues/9)) | Deterministic: specialists received **zero** history on every browser turn |
| "telemetry depth: no metrics provider" ([#19](https://github.com/nitin27may/e-commerce-agents/issues/19)) | Metrics were never the gap; .NET spans were **invisible in Aspire's GenAI view** |
| ".NET tests only for ch01–11" ([#20](https://github.com/nitin27may/e-commerce-agents/issues/20)) | **No CI job built any** of the 31 tutorial projects; ch08 was fully broken |
| "`semantic_search` dead under replay" ([#52](https://github.com/nitin27may/e-commerce-agents/issues/52)) | Also a **production** IVFFlat bug returning unrelated products |
| "`optimize_cart` divides by zero" ([#51](https://github.com/nitin27may/e-commerce-agents/issues/51)) | **No promotion had ever applied correctly**, in any environment |
| "`--dotnet` needs a look" (plan 16) | The .NET orchestrator **could not reach any specialist**; 39 of 46 tools were registered under a name the shared prompt corpus never uses |
| "`handoff` mode is slow and verbose" | It **never handed off at all** — 5,403 streamed updates, 23,637 characters, no specialist invoked |
| "`workflow:pre-purchase` discards its own work" | The synthesis was faithful; **two of its four inputs were silently missing** |

## What the pattern is

**A healthy-looking system is the normal presentation of a broken one.**

The .NET stack could not answer a single question for an extended period. Throughout,
twelve containers reported healthy, login worked, the UI served, image builds passed, and
556 unit tests were green. The only symptom was a polite sentence:

> "I tried searching the catalog for running shoes and their prices, but there was an
> error retrieving the results."

The same shape recurs. A stale database volume produced *"I couldn't access the available
products right now"*. A tool returning bare `null` produced *"there may be a temporary
issue accessing your order data"* — to a customer with eleven orders. In each case the
apology was the only evidence, and it reads to a user like an empty catalogue or a flaky
network rather than a defect.

## Why reading does not find these

Four of the entries above were invisible to code review by construction:

- **The tool-naming mismatch** was correct C# and correct YAML. The defect existed only in
  the relationship between them, and nothing compiles that relationship.
- **`handoff` never handing off** looked like a working mesh. Every participant was wired,
  the builder was used correctly, and the start agent simply had a tool it preferred.
- **Tools returning `null`** is idiomatic. It is only wrong because the reader is a
  language model that needs to be told what to do next.
- **The tutorial CI gap** was a workflow that built nothing. The file existed and the jobs
  were green.

Two of the five original entries were found *only because a gate was switched on*. That
is the argument for building gates before content.

## What this costs

The parity gate that catches this class — `web/e2e/orchestration-parity.spec.ts` run
against both backends — **is not in CI**. It needs a full stack and a real API key, which
is impractical on every push, so it runs by hand. That is the standing weakness, recorded
in [ADR 0005](adr/0005-dual-stack-parity.md) rather than smoothed over.

It is also why the numbers here keep growing.

## Related

- [Architecture Decision Records](adr/README.md) — the decisions this pattern shaped
- [Python vs .NET parity matrix](parity-matrix.md) — what still differs, and why
- [Roadmap](roadmap.md) — what is unshipped, marked unshipped
