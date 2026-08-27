# ADR 0002 — No text-to-SQL; tools own their queries

**Status:** Accepted · **Date:** 2026-08-26 (recorded; decided much earlier)

## Context

Agents answer questions about a 34-table Postgres schema. The tempting shortcut is to
hand the model the schema and let it write SQL — it is one tool instead of forty-six,
and it answers questions nobody anticipated.

## Decision

The model never writes SQL. Every query lives in a hand-written tool with parameterised
arguments, and the model chooses among tools rather than composing queries.

## Why

**Row-level scoping is a contract the model cannot be trusted to honour.** Every
user-facing query filters on `user_email` or `user_id`, taken from a ContextVar the
request sets — not from anything the model said. `docs/roadmap.md` states the
consequence plainly: dynamic SQL would bypass that contract. A model that can write
`WHERE` can write the wrong one, and the failure is a customer reading another
customer's orders.

**Parameterisation is not optional and not negotiable.** All queries use `$1, $2`
placeholders through asyncpg. Generated SQL reintroduces injection as a model-behaviour
problem, which is exactly the class of problem this repo spends the most effort
defending against elsewhere.

**Auditability.** A fixed tool surface can be reviewed once. Generated SQL has to be
reviewed every time it runs, by something.

## Consequences

Forty-six tools instead of one, and a question outside their surface simply cannot be
answered. That is the accepted cost.

The planned mitigation is a **typed filter DSL** — a structured `ProductFilters` model
(category, price, brand, sort) replacing `search_products`' flat parameter list. That
gives the model flexibility at the boundary while SQL generation stays server-side and
auditable. It is on the roadmap and not yet built.

## What would make this wrong

A read-only replica with row-level security enforced by the *database* rather than by
application code would remove the scoping argument, since the model could no longer
express a query that crosses a tenant boundary. The injection and audit arguments would
still stand.
