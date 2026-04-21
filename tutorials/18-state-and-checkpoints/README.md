# Chapter 18 — State and Checkpoints

> **Post:** [https://nitinksingh.com/posts/maf-v1-18-state-and-checkpoints/](https://nitinksingh.com/posts/maf-v1-18-state-and-checkpoints/) — concept, diagrams, walkthrough.

Make workflow state durable: executors snapshot at superstep boundaries, storage backends persist the snapshots, a fresh process rehydrates them and the workflow carries on.

## Run the demos

**Prerequisites:** completed [Chapter 17 — Human-in-the-Loop](../17-human-in-the-loop/).

**Environment variables:** none — this chapter uses integer accumulation, no LLM calls.

### Python

```bash
cd tutorials/18-state-and-checkpoints/python
uv sync
uv run python main.py       # writes checkpoint to ./checkpoints/
uv run pytest -v
```

### .NET

```bash
cd tutorials/18-state-and-checkpoints/dotnet
dotnet run
dotnet test
```

## What's in this folder

- [`python/`](./python/) — Python example + 8 tests covering save/restore semantics
- [`dotnet/`](./dotnet/) — .NET example + tests (`[MessageHandler]` executors with `OnCheckpointRestoredAsync`)

## Learn more

- **Full article:** [maf-v1-18-state-and-checkpoints](https://nitinksingh.com/posts/maf-v1-18-state-and-checkpoints/)
- [Series index](../README.md) · Previous: [Ch17](../17-human-in-the-loop/) · Next: [Ch19 Declarative Workflows](../19-declarative-workflows/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
