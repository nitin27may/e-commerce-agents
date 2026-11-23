# Replay mode: running without an API key

How this project runs its tutorials, tests, eval suites and demo stack without calling a model.
This page is for anyone who wants to try or change the project without a paid key, and for anyone
whose run just reported a "replay miss".

## What `LLM_PROVIDER=replay` does

With `LLM_PROVIDER=replay` the chat client is `ReplayChatClient`
(`agents/python/shared/replay_client.py`). It never contacts a model. For each model call it:

1. Builds a canonical form of the request: every message in order, the tool schemas offered, the
   system instructions and, when one is set, the structured-output format.
2. Hashes it (`_request_hash`) and looks for `<hash>.json` in `REPLAY_FIXTURES_DIR`.
3. Returns the response stored in that file.

The stored response is one model turn as the provider returned it. If it contains a tool call, the
framework runs the real local tool and calls the client again with the result appended. So tools,
the database and the agent-to-agent hops all run for real; only the model's words are recorded. A
loop with N model calls uses N recordings.

Sampling settings such as temperature are not part of the key. In tool results, system messages
and instructions, UUIDs and timestamps are replaced with placeholders before hashing
(`_normalize_for_hash`), because those values differ every time the database is seeded. The file on
disk keeps the raw request.

Recording uses the same client. `RECORD=true` with `REPLAY_RECORD_PROVIDER` (`openai`, `azure` or
`anthropic`) makes a real call when a recording is missing and saves the answer. It records on a
miss only: an existing recording is replayed, not replaced.

## Where it is used

| Where | Command | Recordings |
|---|---|---|
| Tutorials | `uv run --project tutorials python tutorials/01-first-agent/python/main.py` | each chapter's `tests/fixtures/replay/` |
| Tests | `cd agents/python && uv run pytest` and `uv run --project tutorials pytest tutorials/ -m "not integration"` | committed beside the tests |
| Eval replay regression | `scripts/replay-evals.sh --setup` | `agents/python/evals/fixtures/replay/` |
| The no-key stack | `./scripts/dev.sh --demo --no-key` | `/app/demo/fixtures/replay` inside the images |

**Tutorials.** A chapter needs no setting at all. `tutorials/_shared/llm.py` (`resolve_provider`)
uses an explicit `LLM_PROVIDER` if there is one, then a key if one is in the environment, and
otherwise falls back to replay and prints one line saying so. The tutorials have their own copy of
the client, `tutorials/_shared/replay_client.py`, whose key has no normalisation: the chapters do
not touch the database.

**Tests.** Tests that run an agent through a model turn replay committed recordings. The tutorial
tests marked `integration` are the live ones; `-m "not integration"` leaves them out, which is what
CI does.

**Eval replay regression.** `scripts/replay-evals.sh` is the `replay-regression` job in
`.github/workflows/evals.yml`, and CI runs that same script. It starts the five specialists in
replay mode, runs every quality suite and the safety suite, and compares each to its committed
baseline. `--setup` first migrates and seeds the database; it needs Postgres and Redis
(`docker compose up -d db redis`). See [Agent Quality](agent-quality.md) and the
[eval scorecard](eval-scorecard.md).

**The no-key stack.** `--no-key` layers `docker-compose.nokey.yml` over the compose file. The
overlay sets `LLM_PROVIDER=replay` and pins, as literals, every setting that shapes a prompt or a
tool schema, so a `.env` file or an exported variable cannot change what a recording was keyed on.
It is Python only and cannot be combined with `--dotnet`.

> **The released images do not yet contain the recordings for the `--no-key` demo.** Today every
> chat prompt on that stack is reported as not recorded. Browsing, search, the cart and checkout
> call no model and work normally. See the [demo guide](demo-guide.md#no-key-mode).

## What a replay miss is

A replay miss means no recording matches the request. The client raises
`ReplayFixtureMissingError`; it never falls back to a live call unless `RECORD=true`. What you see
depends on where you are:

- **A tutorial or a test** fails with `No replay fixture for this request (hash=…)` and the path
  it looked for.
- **An eval run** counts the case as never run, prints how many cases had no recording, and exits
  non-zero whatever the score. Treat the scores of that run as incomplete, not as a regression.
- **The chat UI** shows a notice in place of an answer. The default text is "This demo replays
  recorded model responses, and this prompt was not recorded. Try one of the suggested prompts
  instead." The stack stays up and the missed message is not kept in the conversation.

A miss inside a specialist travels back to the orchestrator as HTTP 424 on `/message:send` or an
`event: replay_miss` frame on `/message:stream` (`agents/python/shared/replay_miss.py`). Nothing
about the missed request is put on the wire.

Two things produce misses that are not missing recordings. A second eval run on the same machine
collides on the specialists' fixed ports, 8081 to 8085. Re-seeding a database that is already
seeded changes tool results. Run one at a time, from a fresh database; for the no-key stack that
means `--clean` when you start it again.

## When recordings must be re-made

The key is the exact request, so any change to what the model is sent makes the old recording
unreachable:

- **A prompt.** The composed system prompt is in the key, shared fragments included. Editing one
  file under `agents/python/config/prompts/_shared/` invalidates the recordings of every agent.
- **A tool schema.** A renamed tool, a new parameter or a changed description.
- **A tool's output shape.** A tool result is a message in the next model call.
- **A dataset case.** A new or reworded question is a new request.

The recipe, with its traps, is in
[`agents/python/evals/fixtures/README.md`](https://github.com/nitin27may/e-commerce-agents/blob/main/agents/python/evals/fixtures/README.md).
Recording needs a real key. If only the hashing scheme changed, `evals/rehash_fixtures.py` re-keys
the stored requests offline, with no key.

## What replay cannot do

- **Answer a prompt that was not recorded.** There is no model behind it. A reworded question is a
  miss.
- **Measure latency or cost.** Replay latency is the time to read a JSON file, and the committed
  baselines carry no token, cost or latency fields.
- **Say how a model behaves today.** A passing replay run shows that the recorded answers still
  pass the current code and prompts. Scores also vary between recording sessions, so a score
  belongs to the session that recorded it.
- **Stream token by token.** A replayed response arrives as one update per message.
- **Hide the calendar entirely.** A few tools filter or bucket on the current date, so a recording
  can go stale as time passes. The fixtures README lists the known cases.

## The .NET counterpart

The .NET stack has the same provider. `ReplayChatClient`
(`agents/dotnet/src/ECommerceAgents.Shared/Agents/ReplayChatClient.cs`) looks a recording up by a
hash of the messages, tools and instructions, and a miss is fatal unless `RECORD=true`. The .NET
eval workflow (`.github/workflows/evals-dotnet.yml`) runs on it by default and is started by hand.

The .NET tutorial tests use something simpler. `ScriptedChatClient`
(`tutorials/_shared/dotnet/ScriptedChatClient.cs`) answers with strings the test supplies, in
order, and records what each call received: the instructions, the messages and the tools offered.
A test then asserts on how the framework wired the agent. It needs no key and no recording, so
`dotnet test` is the keyless path for a chapter's .NET half, while `dotnet run` calls a live model.
