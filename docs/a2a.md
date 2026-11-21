# A2A 1.0 on both stacks

Every specialist, Python and .NET, serves two wires on the same port:

| Wire | Where | Status |
|---|---|---|
| **A2A 1.0**, JSON-RPC binding | `POST /a2a/v1`, card at `GET /.well-known/agent-card.json` | On both stacks. What the official `a2a-sdk` / A2A .NET SDK client, and any other 1.0 client, speaks |
| **Legacy** | `POST /message:send`, `POST /message:stream` | The original look-alike: a bare `{"message": "..."}` body. Unchanged on both stacks, byte for byte |

The caller picks one with `A2A_TRANSPORT=legacy|a2a` (default `legacy`), on **both** orchestrators. It applies to the
orchestrator's `call_specialist_agent` / `OrchestratorTools.CallSpecialistAgent` and to the handoff mesh's remote
participant (`RemoteSpecialistChatClient` on each stack). Nothing about the legacy routes changed, so an
orchestrator on `legacy` talks to a specialist of either stack exactly as before. The legacy routes retire one
release after both stacks default to `a2a`.

Because both stacks serve and speak the same wire, **any orchestrator can call any specialist over `a2a`**: Python to
.NET, .NET to Python, and each to its own kind. That is checked, not assumed (see *Interop between the stacks*).

## Which spec, and why

**A2A 1.0, JSON-RPC binding.**

| | Python | .NET |
|---|---|---|
| Package | `a2a-sdk` **1.3.0** (`PROTOCOL_VERSION_CURRENT == "1.0"`), pinned exactly in `agents/python/pyproject.toml` | `A2A` and `A2A.AspNetCore` **1.0.0-preview4** (the official `a2aproject/a2a-dotnet` SDK, spec 1.0), pinned exactly in `agents/dotnet/Directory.Packages.props` |
| Release state | stable | **preview**: pinned exactly and grouped with the other protocol packages in Dependabot (plan decision B-d6) |
| Server | `DefaultRequestHandlerV2` + a custom `SpecialistExecutor` | `A2AServer` subclassed (`SpecialistA2AServer`) + a custom `IAgentHandler` (`SpecialistA2AExecutor`) mapped with `MapA2A` |
| Client | the SDK client (`shared/a2a_client.py`) | the SDK client (`A2AProtocolClient`, behind `A2AClient`) |

- 1.0 is the version both SDKs implement natively. The card's interface says `protocolVersion: "1.0"`. Python's
  SDK refuses a client that sends no `A2A-Version` header (it would be 0.3); the .NET SDK serves 0.3 and 1.0. The 0.3
  compatibility shim is **not** switched on in Python: nothing in this repo needs it, and an untested path is not a feature.
- JSON-RPC only. 1.0's HTTP+JSON binding puts `POST /message:send` at the root, the paths the legacy routes occupy with
  an incompatible body, so both cannot be mounted. Everything the spec defines lives under `/a2a/v1`; gRPC is not
  mounted either (plan decision B-d2). The .NET SDK's `MapHttpA2A` is deliberately not used.

## What is served

The card (`/.well-known/agent-card.json`, public like `/health`) carries, built from the live agent so it cannot drift:
name and description; one JSON-RPC interface whose URL is the host the caller reached the card by (set
`A2A_PUBLIC_BASE_URL` behind a proxy that rewrites it); `streaming: true`, `pushNotifications: false`; the three
extensions below; one skill per tool that the least-privileged role (`customer`) may call (its name and description
verbatim; an MCP toolset has no static names and is skipped); and the two ways to authenticate (`X-Agent-Secret`, or an
AS-issued bearer service token in `AUTH_MODE=oauth`). The card is public, so a tool restricted to roles that exclude
`customer` (seller or admin tooling) is not listed. Where that restriction is recorded differs by stack but means the
same thing: Python's `requires_role` writes its roles on the function it wraps; .NET's `[RequiresRole]` attribute is
metadata on the tool method, and `A2ARoleMetadataTests` cross-checks every `RoleGuard.Ensure` guard clause against it so
the two cannot drift.

JSON-RPC methods: `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`, `SubscribeToTask`. Push
notifications are not offered, so those methods answer `PushNotificationNotSupportedError`.

Authentication is the same middleware as the legacy routes (`AgentAuthMiddleware`, one per stack): the inter-agent
credential, plus the identity of the user the call is for. How that identity travels is `A2A_IDENTITY`
(`headers` | `token` | `both`, default `both`): the forwarded `X-User-*` headers, or a **signed, short-lived,
audience-bound `X-User-Context` token** minted by the orchestrator. See *Identity: the signed user-context token*.

## Lifecycle

`submitted` → `working` → `completed` | `failed`. `input-required` and `auth-required` are reserved for cross-agent
approvals (workstream D) and never produced. A message with no text is `rejected` and never reaches the model.

A streamed task's first event is the `Task` in `submitted`, then status updates and artifact chunks, and the last event
is the single terminal status update. A blocking `SendMessage` returns the finished `Task`.

An agent error is a `failed` task whose message is the fixed text `Agent processing failed`: the exception's own text
can name a table or a prompt and never leaves the process.

## How the legacy contract maps

| Legacy | A2A 1.0 |
|---|---|
| `x-session-id` header | the message's **`contextId`** (a UUID). The header is still read as a fallback |
| `response` / the `data:` text frames | one artifact (`answer`): the first chunk `append=false`, later chunks `append=true`, the last `last_chunk=true`. The blocking task carries the aggregated artifact |
| `steps` array / `event: step` frames | `TaskStatusUpdateEvent.metadata[steps]`, extension below |
| `usage` field / `event: usage` frame | `metadata[usage]` on the terminal status update |
| HTTP 424 `replay_miss` / `event: replay_miss` | a `failed` task carrying `metadata[replay-miss]` |
| `[ERROR: ...]` frame | a `failed` task |

### Extensions

Declared in `capabilities.extensions`, all optional. Metadata for one is only sent when the caller listed its URI in the
`A2A-Extensions` request header, so a client that knows nothing about them sees plain A2A. The URIs are identifiers under
`https://github.com/nitin27may/e-commerce-agents/a2a/ext/`:

| URI suffix | Carried in | Shape |
|---|---|---|
| `steps/v1` | status-update metadata | `{steps: [...], offset: n}`: the tool-call steps starting at index `n`. Live updates carry what is new; the terminal update repeats all of them with `offset: 0`. A step is `{tool_name, tool_input, tool_output, status, duration_ms, agent}` (snake_case on both stacks) |
| `usage/v1` | terminal status-update metadata | the wire usage object (`input_token_count`, `output_token_count`, `cached_input_token_count`, `model`), the same shape as the legacy field. It is accounting and never part of the answer text |
| `replay-miss/v1` | failed status-update metadata | `{error: "replay_miss"}`, only under `LLM_PROVIDER=replay` |

The Python SDK merges a status update's metadata into `Task.metadata` (last write wins per key), which is why the terminal
update repeats the steps: a blocking response then carries every step. **The .NET SDK does not merge it**, so the .NET
specialist's task store (`OwnedBoundedTaskStore`) republishes the extension metadata into the stored task, and a
blocking `SendMessage` returns the same `Task.metadata` on either stack. Both servers echo the activated extensions in an
`A2A-Extensions` response header (comma and space separated; the .NET SDK does it itself, the Python SDK does not, so
`_SafeJsonRpcDispatcher` adds it): only URIs the caller listed **and** the server supports, on blocking and streaming
responses, and no header when there are none. The order is the server's declared one on Python; the .NET SDK fixes
none, so tests compare the set.

**The legacy wire spells a step per stack.** A Python specialist writes `tool_name` / `duration_ms` (one
`event: step` frame per step); a .NET specialist's `AgentHost` writes the framework's defaults: camelCase in a blocking
`/message:send` body (`toolName`, `durationMs`, a null `agent`) and PascalCase in `/message:stream`'s `event: steps`
frame, which carries a *list*. Each orchestrator's legacy reader accepts both: `TolerantStepConverter` on .NET and
`shared/legacy_wire.py` on Python (names compared ignoring case and underscores; a missing agent is filled with the
specialist's name; the plural frame is split into live `step` frames). Before this, the Python orchestrator dropped a .NET
specialist's blocking steps and read its `steps` frame as answer text. A specialist's steps are bounded on both stacks: a step keeps only its six canonical keys (Python also a bounded `provenance`), one frame or body contributes at most 50 steps and a turn collects at most 200 (`legacy_wire.MAX_STEPS_PER_*`, `A2AClient.MaxStepsPer*`). Usage needs no tolerance: both stacks name its
fields explicitly. `scripts/a2a-interop.sh` checks it against a real .NET specialist.

**Numbers on the wire.** A protobuf `Struct` (Python's metadata) writes every number as a float, so a .NET reader sees
`5.0`, `0.0`. The .NET wire readers accept whole-valued floats; a reader that wants an integer silently dropped a
Python specialist's steps and usage until the cross-process check found it.

## What the specialist's model sees

Identical on both wires, on both stacks; this is the invariant the design protects. Replay fixtures are keyed by a hash
of the exact request (messages, tool schemas, instructions), so anything that changed the text reaching the model would
invalidate them. Each stack's executor feeds the message text and the session history through the same function its
legacy streaming route uses (`agent_host._run_agent_native_stream` in Python,
`AgentHost.RunAgentWithHistoryStreamingAsync` in .NET). Python's is deliberately **not** `agent-framework-a2a`'s
`A2AExecutor`, which builds a MAF session from `contextId` and would change those messages. Tests assert the model's messages
are equal on both wires (`test_a2a_conformance.py`, `TheModelSeesTheSameMessages_OnBothWires`), including rehydrated
history in Python.

History is rehydrated from Postgres by the `contextId`, scoped to the forwarded caller exactly as before. A `contextId`
the server invented (the caller sent none: an anonymous user) is never queried: Python prefixes its invented ids
`a2a-ctx-`; the .NET SDK mints a GUID, so the executor uses the SDK's `ClientProvidedContextId` flag instead.

## Identity: the signed user-context token

Until B-14 a specialist trusted `X-User-Email` / `X-User-Role` / `X-Session-Id` from anyone holding the shared
`X-Agent-Secret`: the credential says which *agent* is calling, and nothing bound the second claim (which *user*) to it.

`A2A_IDENTITY` (both stacks):

| Mode | Orchestrator sends | Specialist accepts |
|---|---|---|
| `headers` | the `X-User-*` headers only (the original way) | the headers; any `X-User-Context` is ignored |
| `both` (**default**, for one release) | the token **and** the headers | a token when present (a bad one is refused, never ignored; a header that contradicts it is refused); the headers when there is none. A mixed or half-upgraded deployment keeps working |
| `token` | the token only (no `X-User-*`) | a verified token only; headers are not read for identity |

**Token** (`shared/identity_token.py`, `Auth/UserContextToken.cs`; same format and key on both stacks, pinned by
`agents/python/config/identity/testdata/user-context.v1.json`, which each stack verifies): a JWS compact JWT, `alg=HS256`,
header `typ=a2a-user-context+jwt` (a user's login JWT, also HS256, can never be mistaken for one); claims `iss`
(`ecommerce-agents/orchestrator`), `aud` (**the specialist's registry name**: a token for one specialist is no use at
another), `sub` (the email, or `anonymous`), `email`, `role`, `sid` (the session id), `act` (`{"client_id":
"orchestrator"}`, RFC 8693), `iat`, `exp`, `jti`. Lifetime `A2A_IDENTITY_TTL_SECONDS` (default 300, 30 to 900) and a
verifier refuses any token living longer than 15 minutes, one whose `iat` or `nbf` is in the future (beyond 30 seconds of
leeway), one with a missing `exp`/`iat`/`iss`/`aud`/`sub`/`email`/`role`, and one whose `iat`/`exp`/`nbf` is not a whole
number. Both stacks judge every token the same way: `user-context.v1.json` also carries a `negative` list that each
stack must refuse. Minted per call; nothing is stored.

**Key**: HMAC-SHA256 over a key derived with HKDF-SHA256 (RFC 5869, no salt, fixed `info` string) from
`A2A_IDENTITY_KEY` if set, else `AGENT_SHARED_SECRET`. No new identity provider, nothing new to distribute in the
default setup, and the same on both stacks.

**What it buys, and what it does not.** The key is symmetric: a specialist can verify a token and could also mint one,
so a *compromised specialist* is not stopped by this. It adds: a request cannot be replayed at *another* specialist or
after it expires; and a header forged beside a valid token is refused. A caller that holds only `X-Agent-Secret` (a
leaked compose file, an SSRF) can no longer choose its user **only when both** hold: `A2A_IDENTITY=token` **and**
`A2A_IDENTITY_KEY` set to a value **different from** the shared secret. Under the default `both`, a caller that omits
the token and sends forged `X-User-*` headers is believed (that is what keeps a half-upgraded deployment working), and
with no distinct key the shared secret is also the signing key, so holding it is holding the key. Starting in `token`
mode with the key empty or equal to `AGENT_SHARED_SECRET` logs a warning (`settings.a2a_identity_key_not_distinct`, on
both stacks; the key is never logged).

**No replay protection within the lifetime.** `jti` is minted, and nothing checks it: no specialist keeps a seen-`jti`
set. A captured request replays at the *same* specialist until `exp` plus the 30 second leeway (default five minutes,
at most 15). `jti` is for audit correlation only today. A replay cache (bounded, per specialist, keyed by `jti` until
`exp`) is the follow-up if that window matters. Asymmetric signing (the orchestrator alone holding a private key) is the follow-up that closes
the compromised-specialist case; it needs key distribution this repo does not have outside `AUTH_MODE=oauth`.

**Default and rollout**: `both`. Moving the default to `token` is the next release's step, once the mixed-stack
compose runs (`scripts/e2e-both-stacks.sh --mixed`) pass with it; the compose smoke paths were not runnable where this
was built. The plan row asked for an auth-server RFC 8693 token exchange verified against JWKS; the auth-server only
runs in `AUTH_MODE=oauth`, so that design would have put it into the default topology. The orchestrator mints instead.

## Operating notes

- **Tasks are not retained.** The in-memory task store is bounded: the newest 256 tasks are kept and the rest deleted
  (`BoundedTaskStore` in Python, `OwnedBoundedTaskStore` in .NET). The bound is process-wide (a busy caller can push
  another's finished tasks out); *reading* a task is scoped to the user who made it, on both stacks (a task id is not a
  capability: get, list, cancel, subscribe and continuing a task by id in a message all answer "not found" for another
  user's task). The .NET SDK's store interface passes no caller to the store, so ownership there is read from the
  forwarded identity the auth middleware stamped, which flows into the SDK's background execution.
- **Anonymous callers read no stored task.** A caller with no resolved user (an unauthenticated chat, which the
  orchestrator forwards with an empty email, or a health/system call with no identity at all, stamped `system`) shares
  one task owner, so owner scoping alone would let one read another's tasks (`ListTasks` would list them all). Such a
  caller gets "task not found" from `GetTask`, `SubscribeToTask` and a message that continues a task by id, and an empty
  `ListTasks`; the task its own `SendMessage` / `SendStreamingMessage` response returns is all it needs, and it is still
  served. `CancelTask` stays open to them on purpose: the orchestrator's best-effort cancel of an anonymous stream it
  abandoned is a legitimate anonymous call, it needs the task id (a random id only that stream's response carries), and
  refusing it would leave the abandoned task running. The per-process and anonymous-bucket active-task caps still apply.
  Both stacks.
- **Running tasks are capped.** A task outlives the connection that started it, so a burst of slow or dropped callers is
  a burst of concurrent model calls. `ActiveTaskLimiter` (both stacks) counts the tasks whose executor is running, per
  process (`A2A_MAX_ACTIVE_TASKS`, default 32) and per forwarded user (`A2A_MAX_ACTIVE_TASKS_PER_CALLER`, default 8;
  anonymous callers share one bucket). Over either cap, `SendMessage` / `SendStreamingMessage` answer a JSON-RPC error
  (`Too many active tasks; retry later`, code `-32603`: the spec defines no "busy" code) before any task exists; if a race
  slips past that pre-check the executor refuses atomically and the task ends `rejected`. Nothing queues. The slot is
  released when the executor ends, including when `CancelTask` stops it. Out-of-range values fail at startup on both
  stacks.
- **Errors outside the executor say nothing.** An exception that is not a protocol error (a failing task store, a bug in
  the handler) is answered with a fixed text (`Internal error`) and its detail is logged server-side through `log_safe` /
  `LogSafe.Clean`. The .NET SDK's endpoint already masks the generic case; `SpecialistA2AServer` also logs it.
- **SSE and U+2028.** The Python SDK server writes SSE JSON with raw `U+2028`, `U+2029` and `U+0085`, which its own
  client splits lines on, so a model reply holding one truncated the JSON; the dispatcher is wrapped to escape them. The
  .NET SDK escapes them already (a test pins it, and the cross-process check sends one to the Python client).
- **The client retries only what provably never ran.** A streamed call is re-sent over the blocking call only if the
  connection was never made, the card was unusable, or the specialist itself refused with a status that is not 502/504; a
  read timeout, a dropped connection, a gateway 502/504 and a JSON-RPC error the specialist *returned* prove nothing,
  and after a frame has arrived it is never re-sent (its tools may have run). The SDKs differ in how they surface
  transport errors (Python wraps every `httpx` error in `A2AClientError`, unwrapped by `shared.a2a_client.root_cause`;
  .NET's SDK client throws `HttpRequestException` directly) but `A2AProtocolClient.NeverRan` and the Python
  `_request_never_ran` implement the same rule. The legacy .NET client never fell back from stream to blocking and
  still does not; its Polly retry covers establishing the connection.
- **A disconnect does not cancel the run, so the client cancels it.** The SDK owns a task independently of the connection
  that started it, so on either stack a streamed call that is abandoned, times out or fails sends a bounded (3 s)
  best-effort `CancelTask` that never raises and never replaces the original error. A *blocking* `SendMessage` learns
  the task id only from its response, so a blocking call that times out cannot be cancelled by the client (the per-caller
  cap bounds those).
- **The client only ever talks to the registry origin.** The card's interface URL comes from the request's `Host` header
  or `A2A_PUBLIC_BASE_URL`, and the client carries `X-Agent-Secret` and the forwarded identity on every request. A card
  naming an interface whose origin (scheme, host, effective port) differs from the `AGENT_REGISTRY` entry is refused as
  a card problem: no message and no credential is sent to that origin, and the card is not cached. Redirects are never
  followed (the .NET client refuses any 3xx, and the orchestrator's named HTTP client no longer follows them on either wire).
  Consequence: `A2A_PUBLIC_BASE_URL` must name the same origin the registry uses to reach the specialist.
- The client caches each specialist's card for five minutes.

## Interop between the stacks

`scripts/a2a-interop.sh` starts real specialists on real sockets (no key, no database, no Docker) around scripted models
and drives each with the *other* stack's client:

| Direction | Server | Client | Test |
|---|---|---|---|
| Python → .NET | `agents/dotnet/tests/ECommerceAgents.A2AInteropHost` (the real `AgentHost`: auth middleware, step recorder, legacy routes, A2A) | the official `a2a-sdk` client and `shared.a2a_client` | `agents/python/tests/test_a2a_dotnet_interop.py` |
| .NET → Python | `agents/python/tests/a2a_interop_server.py` (the production specialist app) | the official A2A .NET SDK client and `A2AClient` under `A2A_TRANSPORT=a2a` | `agents/dotnet/tests/ECommerceAgents.Shared.Tests/A2AInteropTests.cs` |

Each is skipped unless `DOTNET_SPECIALIST_URL` / `PYTHON_SPECIALIST_URL` (and `_FAIL`, `_MISS`, `_WEIRD`, `_TOKEN`) name a
running peer, so the plain unit suites stay hermetic. They cover the card, blocking and streamed tasks, artifact
chunking, extension metadata only when asked, `contextId`, auth, owner-scoped tasks, failures without error text, a
replay miss, awkward characters, and the identity token in both directions (a token minted by one stack served by the other;
refused: headers alone in `token` mode, a forged header beside a valid token, a token for another specialist, another key,
an expired one). CI runs it as the `a2a-interop` job (keyless).

The mixed-stack compose overlay (`docker-compose.mixed.yml`, `scripts/e2e-both-stacks.sh --mixed`) puts one stack's
orchestrator in front of the other's specialists for the browser-level run; it was validated with `docker compose config`
only, and the Playwright run needs a live key.

## Testing

Per stack, each drives a real specialist host (real agent, tool loop, step recorder and auth middleware; only the model
is scripted) with the official SDK client: Python `agents/python/tests/test_a2a_conformance.py` and
`test_a2a_transport.py`; .NET `A2ASpecialistServerTests` (the same cases) and `A2AClientTransportTests`.

```bash
cd agents/python
uv run pytest tests/test_a2a_conformance.py tests/test_a2a_transport.py tests/test_identity_token.py -v
cd ../dotnet && dotnet test tests/ECommerceAgents.Shared.Tests --filter "FullyQualifiedName~A2A|FullyQualifiedName~UserContextToken"
cd ../.. && scripts/a2a-interop.sh        # both directions, across processes
# The replay gate over the new wire (zero missing fixtures, scores equal to the baselines):
A2A_TRANSPORT=a2a scripts/replay-evals.sh --setup
```

## Not done

`input-required` for approvals, push notifications, the HTTP+JSON and gRPC bindings, the 0.3 compatibility shim in
Python, asymmetric signing of the identity token, a golden-file contract test shared by both stacks (the live
cross-process check replaced it).

## Replay misses

**Only a replaying process acts on a miss.** Every orchestrator-side check (the 424 status, the `replay_miss` frame, the `replay-miss/v1` metadata, a workflow's miss event, the tool's catch, the chat route's discard) is gated on that orchestrator's own `LLM_PROVIDER` being `replay` (`shared.replay_miss.replay_mode_active`, `ReplayMiss.IsActive`). Elsewhere a 424 or a miss frame is an ordinary specialist failure (the "returned an error" sentence, or an interrupted stream) and the user's message is kept. The specialist side still emits the signals (they only arise under replay).

Under `LLM_PROVIDER=replay` a prompt with no recording is a *miss*, on both stacks. A specialist reports it as HTTP 424
`{"error":"replay_miss"}` (legacy `/message:send`), an `event: replay_miss` frame then `[DONE]` (legacy
`/message:stream`), or a failed task with `replay-miss/v1` metadata (A2A 1.0). Each orchestrator's client raises it
(`SpecialistReplayMissError` / `SpecialistReplayMissException`) rather than returning an apology the model would
answer, the tool loop ends without another model call, and the chat route answers `replay_miss: {message}` on
`/api/chat` or an `event: replay_miss` frame, `metadata` and `done` on the stream, saves no assistant message, logs no
usage and deletes the unanswered user message. Both stacks do all of this; the .NET specialists' legacy routes emit the
424 / frame too (they used to answer 500).
