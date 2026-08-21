# Eval replay fixtures

Recorded LLM cassettes for `evals/datasets/*.json`, keyed by request hash
(see `shared/replay_client.py`). With `LLM_PROVIDER=replay`, running any
quality or safety suite makes zero LLM network calls and needs no
credentials — the same mechanism `tutorials/` uses.

## Re-recording after a dataset or prompt change

```bash
cd agents/python
docker compose -f ../../docker-compose.yml up -d db redis   # from repo root
LLM_PROVIDER=replay RECORD=true REPLAY_RECORD_PROVIDER=azure \
  REPLAY_FIXTURES_DIR=evals/fixtures/replay \
  python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json
```

Repeat per specialist dataset (`order-management`, `pricing-promotions`,
`review-sentiment`, `inventory-fulfillment` — DB only, no other services
needed, since `ProductionRunner` calls each specialist in-process).

`orchestrator` and `--suite safety` also need the five specialist A2A
services actually running and reachable (`call_specialist_agent` makes a
real HTTP call — that's independent of `LLM_PROVIDER`, which only controls
the chat *client*, not inter-service transport). To record: start every
specialist **also** in `LLM_PROVIDER=replay RECORD=true` mode, pointed at
the same fixtures directory, before recording the orchestrator/safety
datasets — otherwise the specialist's live (non-deterministic) reply text
becomes part of the orchestrator's own request, so its fixture hash never
matches on a later run:

```bash
for svc in product_discovery:8081 order_management:8082 pricing_promotions:8083 review_sentiment:8084 inventory_fulfillment:8085; do
  name="${svc%%:*}"; port="${svc##*:}"
  AGENT_NAME="$name" AGENT_PORT="$port" LLM_PROVIDER=replay RECORD=true REPLAY_RECORD_PROVIDER=azure \
    REPLAY_FIXTURES_DIR=evals/fixtures/replay \
    uv run --no-project uvicorn "${name}.main:app" --host 0.0.0.0 --port "$port" &
done

LLM_PROVIDER=replay RECORD=true REPLAY_RECORD_PROVIDER=azure \
  REPLAY_FIXTURES_DIR=evals/fixtures/replay \
  AGENT_REGISTRY='{"product-discovery":"http://localhost:8081","order-management":"http://localhost:8082","pricing-promotions":"http://localhost:8083","review-sentiment":"http://localhost:8084","inventory-fulfillment":"http://localhost:8085"}' \
  python -m evals.run_evals --agent orchestrator --dataset evals/datasets/orchestrator_routing.json
# ...and: python -m evals.run_evals --suite safety
```

Verify deterministic replay after recording — same scores, no network, and
this time with every service (including the five specialists) in **pure**
`LLM_PROVIDER=replay` (no `RECORD`) — this is exactly what CI's `smoke` job
does:

```bash
LLM_PROVIDER=replay REPLAY_FIXTURES_DIR=evals/fixtures/replay \
  python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json
```

Confirmed working end to end: with the five specialists running as plain
`LLM_PROVIDER=replay` servers (no key, no `RECORD`) and the orchestrator/
safety suites run the same way, both reproduced their recorded scores
exactly with total latency dropping from tens of seconds to under a second
— proof of zero network calls anywhere in the request graph. So the
`smoke` CI job **does** cover every dataset, including
`orchestrator_routing.json` and `red_team.json` — it just needs to boot the
five specialist services (in replay mode, no cost) as a setup step before
running those two, which the five pure-specialist datasets don't require
at all.

## A real bug this surfaced: non-deterministic product ids broke grounding on replay

`scripts/seed.py`'s `products` table relied on `id UUID ... DEFAULT
gen_random_uuid()` — a fresh random id on every seed run, including CI's,
which always starts from an empty database. A recorded fixture's response
text references the *exact* id the model saw when recorded (e.g. the Sony
WH-1000XM5's card); grounding verification checks that id against whatever
the live database has right now. Reseed with a different id for the same
product, and a real, correct claim starts scoring `not_found` for no
reason but seed-order luck — verified by literally reseeding twice locally
and watching the Sony WH-1000XM5 get two different ids.

Fixed in `scripts/seed.py::product_id_for()`: `uuid.uuid5()` against a
fixed namespace + the product's name, so every environment — local, CI,
anyone's laptop — assigns the exact same id to the exact same product name,
forever. Verified deterministic across three independent truncate+reseed
cycles before trusting it. Order ids were deliberately left random — the
current datasets only ever reference orders by synthetic, intentionally
nonexistent ids (`550e8400-...`, testing the "not found" / "not yours"
paths), so order-id determinism isn't load-bearing today. If a future
dataset adds a case that looks up a *real* seeded order, the same
`uuid5(namespace, stable_key)` treatment would need to extend there too.

## The bigger version of the same bug: live database payloads were in the cache key

The product-id fix above closed the *grounding* half of the problem and left
the *lookup* half open, which is why the `smoke` job stayed red on every PR
that ran it.

A fixture's filename is a hash of `(messages, tools, instructions)`. In a MAF
tool loop, turn N+1's messages contain turn N's `function_result` — raw JSON
straight out of Postgres. So the fixture key included live database payloads,
and CI reseeds a fresh database on every run. Anything in those payloads that
differed between reseeds produced a different key and an unreachable fixture.
Three things did:

1. **Random primary keys.** `reviews.id` and `orders.id` are still
   `gen_random_uuid()` (see the note above on why order ids were left alone).
2. **Timestamps.** Every seeded timestamp is `datetime.now(timezone.utc)`
   plus or minus an offset, down to the microsecond, so no two seed runs ever
   agree.
3. **Tied rows in an unordered result.** SQL leaves the order of rows tied on
   the `ORDER BY` column undefined, and Postgres genuinely returns them
   differently depending on the query plan — which depends on table
   statistics. Freshly-loaded tables have none, so a database seeded seconds
   ago and never analyzed sorts ties differently from a settled one. That is
   exactly the shape CI runs in, and it is why `product_discovery.json`'s
   trending case failed there while passing on every developer's machine.

Fixed in three places:

- `shared/replay_client.py::_normalize_for_hash` replaces UUIDs and
  timestamps with placeholders **when hashing**, for `role: "tool"` messages
  only. The fixture on disk still stores the raw request. Tool *call*
  arguments live in the preceding assistant message and are hashed verbatim,
  so two different calls can never collide on one fixture — read that
  function's docstring for the assumption this depends on.
- The same function ordinalizes provider-assigned `call_id`s, so re-recording
  one turn no longer invalidates every fixture downstream of it.
- `scripts/seed.py` runs `ANALYZE` after loading, so the planner is not
  running blind on fresh tables. Good practice regardless of evals.

`evals/rehash_fixtures.py` re-keys the corpus offline whenever the hashing
scheme changes — it reads each fixture's stored raw request, so it needs no
credentials and never re-records. Note its keeper policy: when two fixtures
collapse onto one key, they recorded *different model trajectories*, and
later turns were recorded against one specific trajectory. It keeps the
sibling with the most consumers; keeping the wrong one strands every fixture
downstream of the discarded trajectory.

Verified by seeding a throwaway database from scratch twice and running all
seven suites against each: identical scores, zero missing fixtures. Every
suite matched its committed baseline exactly.

## Known residual: query-time `NOW()` still moves with the calendar

Normalizing the cache key fixes values that *differ* between reseeds. It
cannot fix a tool payload that changes **shape** as real time passes, and
several queries filter or bucket on `NOW()` at query time:

- `review_sentiment/tools.py::get_sentiment_trend` buckets reviews by
  `DATE_TRUNC('month', created_at)` within a rolling window. Reviews are
  seeded at `now - randint(1, 180) days`, so which calendar month a review
  lands in — and whether it is inside the window at all — depends on the date
  the seed ran. The month *labels* are normalized away; the per-bucket counts
  are not.
- `pricing_promotions/tools.py` has the largest concentration of these,
  filtering coupons and promotions on validity windows that are themselves
  seeded relative to `now`.

So a fixture can still go stale on a different calendar day rather than a
different seed. Closing this properly means quantizing seeded timestamps to
boundaries the queries cannot straddle (month-aligned review dates, for
instance) — the same move `product_id_for()` made for ids. It is tracked
separately rather than bundled into the cache-key fix.

Two smaller residuals worth naming:

- **Unordered ties.** `ANALYZE` makes the planner behave like a normal
  database rather than a freshly-loaded one, but SQL still does not promise
  an order for tied rows. Roughly 25 `ORDER BY` clauses across the specialist
  tools lack a unique tiebreaker. Adding one to each is the real fix; each
  change re-records the fixtures that touch it.
- **Eval runs mutate state.** HITL-gated cases write approval rows, so a
  second run against the same database does not start where the first did.
  Record and replay both need to start from a fresh seed.

## Superseded note: some suites drift slightly across independent reseeds

The paragraph below predates the cache-key fix and understated the problem —
it claimed "this doesn't break replay itself", which is precisely what was
happening. Kept for the history of how the diagnosis moved.

`pricing_promotions.json` and `orchestrator_routing.json` (which calls
`get_active_deals`) showed small score differences (a few points) between
the original recording and a later from-scratch reseed+replay, even after
the product-id fix — `scripts/seed.py` computes coupon/promotion
`valid_from`/`valid_until` windows relative to `datetime.now(timezone.utc)`
*at seed time* (e.g. `now - timedelta(days=30)`), so two reseeds run
minutes apart produce slightly different absolute validity windows. This
doesn't break replay itself — the model's own recorded text is frozen and
byte-identical either way — it changes which *tool-returned facts* end up
being checked, which can nudge groundedness/correctness by a point or two.
`evals/baselines/*.json` were regenerated against the current deterministic
seed as of this fix; `--max-regression` (default 0.05) already tolerates
this scale of drift. Making coupon/promotion windows fully time-independent
(e.g. anchored to a fixed epoch instead of seed-time `now()`) would close
this the same way the product-id fix closed the bigger one, but wasn't
pursued here — the datasets that hit it aren't currently sensitive enough
to need it.
