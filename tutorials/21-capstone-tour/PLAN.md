# Chapter 21 — Capstone Tour

## Goal

Guide the reader through the refactored e-commerce app, pointing at where each MAF concept from Ch01–Ch20 lives in real code.

## Article mapping

- **New chapter** (replaces the role of [Part 0 — Complete Guide](https://nitinksingh.com/posts/building-a-multi-agent-e-commerce-platform-the-complete-guide/))
- **New slug**: `/posts/maf-v1-capstone-tour/`

## Teaching strategy

- [x] Pure article — no new code. References every earlier chapter.

## Deliverables

### `python/`
- None (this is an article-only chapter).

### `dotnet/`
- None.

### Article
- One section per tutorial chapter. Each section: file path + line range in the live app, one-paragraph explanation, screenshot of the Aspire trace for that flow.
- Closing: what's next — the marketplace/catalog layer, multi-tenant patterns, deployment topics.

## Verification

- Every chapter referenced by number has at least one live-code pointer.
- Running the `/docs-sync reverse` audit finds no *undocumented* major surface area.

## How this maps into the capstone

It *is* the capstone article. Published only after Phase 7 lands.

## Out of scope

- Performance tuning — a candidate for a follow-up series.
- Cloud deployment — another follow-up series.
