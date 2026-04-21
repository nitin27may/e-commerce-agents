# Chapter 21 — Capstone Tour

> **Post:** [https://nitinksingh.com/posts/maf-v1-21-capstone-tour/](https://nitinksingh.com/posts/maf-v1-21-capstone-tour/) — concept, diagrams, walkthrough.

Every MAF concept from the prior chapters, mapped to the exact file and line where it lives in the live e-commerce repo.

## Run the capstone

This chapter has no standalone demo — it tours the full capstone application. Bring up the Python stack:

```bash
# from the repo root
./scripts/dev.sh
```

Services once it's up:

- Frontend: [http://localhost:3000](http://localhost:3000)
- Orchestrator API: [http://localhost:8080](http://localhost:8080)
- Aspire Dashboard (telemetry): [http://localhost:18888](http://localhost:18888)

The full-stack .NET variant lives in `docker-compose.dotnet.yml`:

```bash
docker compose -f docker-compose.dotnet.yml --profile agents up --build
```

## Environment variables

Full stack uses the same LLM provider vars as the single-chapter demos — see [Chapter 00 — Setup](../00-setup/) for the complete variable reference.

## What's in this folder

No `python/` or `dotnet/` folder — the "code" for this chapter is the capstone repo itself. The full article walks you through every subsystem with `file:line` citations.

## Learn more

- **Full article:** [maf-v1-21-capstone-tour](https://nitinksingh.com/posts/maf-v1-21-capstone-tour/)
- [Series index](../README.md) · Previous: [Ch20b DevUI](../20b-devui/)
- Shared: [Mermaid style guide](../_shared/mermaid-style-guide.md) · [Jargon glossary](../_shared/jargon-glossary.md)
- **MAF docs:** [Agent Framework overview](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-csharp) · [Journey](https://learn.microsoft.com/en-us/agent-framework/journey/?pivots=programming-language-csharp)
