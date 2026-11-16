# A2A 1.0 on the Python specialists

Every Python specialist serves two wires on the same port:

| Wire | Where | Status |
|---|---|---|
| **A2A 1.0**, JSON-RPC binding | `POST /a2a/v1`, card at `GET /.well-known/agent-card.json` | New (B-12). What the official `a2a-sdk` client, and any other 1.0 client, speaks |
| **Legacy** | `POST /message:send`, `POST /message:stream` | The original look-alike: a bare `{"message": "..."}` body. Unchanged, and still what the .NET specialists serve |

The caller picks one with `A2A_TRANSPORT=legacy|a2a` (default `legacy`). It applies to the orchestrator's
`call_specialist_agent` and to the handoff mesh's `RemoteSpecialistChatClient`. Nothing about the legacy
routes changed, so a Python orchestrator on `legacy` talks to a .NET specialist exactly as before, and a
.NET orchestrator talks to a Python specialist exactly as before. The legacy routes retire one release
after both stacks default to `a2a`.

**`A2A_TRANSPORT=a2a` works against Python specialists only, until B-13 ports the server to .NET.** A .NET
specialist's card has no A2A interface, and the client says so (`advertises no A2A 1.0 JSON-RPC interface;
set A2A_TRANSPORT=legacy`) without sending a message.

## Which spec, and why

**A2A 1.0, JSON-RPC binding**, using `a2a-sdk` 1.3.0 (`PROTOCOL_VERSION_CURRENT == "1.0"`), pinned exactly in
`agents/python/pyproject.toml` the way the MAF packages are.

- 1.0 is the version the SDK's server and client implement natively; 0.3 is only a compatibility shim in it.
  The card's interface says `protocolVersion: "1.0"`, and a client that sends no `A2A-Version` header is treated as 0.3 and
  refused (`VersionNotSupportedError`). The 0.3 shim (`enable_v0_3_compat`) is **not** switched on: nothing
  in this repo needs it, and an untested path is not a feature.
- JSON-RPC only. 1.0's HTTP+JSON binding puts `POST /message:send` at the root, the paths the legacy routes
  occupy with an incompatible body, so both cannot be mounted. Everything the spec defines lives under
  `/a2a/v1`; gRPC is not mounted either (plan decision B-d2).

## What is served

The card (`/.well-known/agent-card.json`, public like `/health`) carries, built from the live agent so it
cannot drift: name and description; one JSON-RPC interface whose URL is the host the caller reached the card
by (set `A2A_PUBLIC_BASE_URL` behind a proxy that rewrites it); `streaming: true`, `pushNotifications:
false`; the three extensions below; one skill per `FunctionTool` (its name and description verbatim; an MCP
toolset has no static names and is skipped); and the two ways to authenticate (`X-Agent-Secret`, or an
AS-issued bearer service token in `AUTH_MODE=oauth`).

JSON-RPC methods: `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`,
`SubscribeToTask`. Push notifications are not offered, so those methods answer
`PushNotificationNotSupportedError`.

Authentication is the same middleware as the legacy routes (`AgentAuthMiddleware`): the inter-agent
credential plus `X-User-Email` / `X-User-Role`, which the executor runs under. Signed identity tokens
instead of forwarded headers are B-14.

## Lifecycle

`submitted` → `working` → `completed` | `failed`. `input-required` and `auth-required` are reserved for
cross-agent approvals (workstream D) and never produced. A message with no text is `rejected` and never
reaches the model.

A streamed task's first event is the `Task` in `submitted`, then status updates and artifact chunks, and the
last event is the single terminal status update. A blocking `SendMessage` returns the finished `Task`.

An agent error is a `failed` task whose message is the fixed text `Agent processing failed`: the exception's
own text can name a table or a prompt and never leaves the process.

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

Declared in `capabilities.extensions`, all optional. Metadata for one is only sent when the caller listed its
URI in the `A2A-Extensions` request header, so a client that knows nothing about them sees plain A2A. The URIs
are identifiers under `https://github.com/nitin27may/e-commerce-agents/a2a/ext/`:

| URI suffix | Carried in | Shape |
|---|---|---|
| `steps/v1` | status-update metadata | `{steps: [...], offset: n}`: the tool-call steps starting at index `n`. Live updates carry what is new; the terminal update repeats all of them with `offset: 0`. The SDK merges status metadata into `Task.metadata` with the last write winning per key, which is why the terminal repeat exists: a blocking response then carries every step |
| `usage/v1` | terminal status-update metadata | the wire usage object (`input_token_count`, `output_token_count`, `cached_input_token_count`, `model`), the same shape as the legacy field. It is accounting and never part of the answer text |
| `replay-miss/v1` | failed status-update metadata | `{error: "replay_miss"}`, only under `LLM_PROVIDER=replay` |

The server does not yet echo the activated extensions back in an `A2A-Extensions` response header.

## What the specialist's model sees

Identical on both wires; this is the invariant the design protects. Replay fixtures are keyed by a hash of
the exact request (messages, tool schemas, instructions), so anything that changed the text reaching the
model would invalidate them. The executor (`shared/a2a_server.py::SpecialistExecutor`) therefore feeds the
message text and the session history through the same `_run_agent_native_stream` the legacy routes use. It is
deliberately **not** `agent-framework-a2a`'s `A2AExecutor`, which builds a MAF session from `contextId` and
would change those messages. `tests/test_a2a_conformance.py` asserts the model's messages are equal on both
wires, including rehydrated history.

History is rehydrated from Postgres by the `contextId`, scoped to the forwarded caller exactly as before. A
`contextId` the server invented (the caller sent none: an anonymous user) is marked `a2a-ctx-...` and is never
queried.

## Operating notes

- **Tasks are not retained.** The SDK's in-memory store would keep every task for the life of the process; a
  bounded store (`BoundedTaskStore`, 256 tasks) keeps the newest, each readable only by the user who made it.
  `GetTask` on a finished task works until it is evicted.
- **SSE and U+2028.** The SDK writes SSE JSON with raw `U+2028`, `U+2029` and `U+0085`, which its own client
  splits lines on, so a model reply holding one truncated the JSON. The dispatcher is wrapped to escape them
  (the same fault `shared/sse.py` prevents on the legacy stream). A conformance test pins it.
- **The client retries only what provably never ran.** The SDK wraps transport errors in `A2AClientError`;
  `shared/a2a_client.py::root_cause` unwraps them so the orchestrator's rule is unchanged: a streamed call is
  re-sent over `SendMessage` only if the connection was never made or the specialist rejected it, and never
  after a frame has arrived (its tools may have run).
- **A disconnect does not cancel the run.** The SDK owns a task independently of the connection that started
  it, so a client that drops a stream leaves the specialist running to completion; on the legacy stream a
  disconnect cancelled the run. `CancelTask` is the way to stop one, and the orchestrator does not call it
  yet.
- **The client only ever talks to the registry origin.** The card's interface URL comes from the request's
  `Host` header or `A2A_PUBLIC_BASE_URL`, and the client carries `X-Agent-Secret` and the forwarded user
  identity on every request. A card naming an interface whose origin (scheme, host, effective port) differs
  from the `AGENT_REGISTRY` entry is refused as a card problem: no message and no credential is sent to that
  origin, and the card is not cached. Redirects are never followed. Consequence: `A2A_PUBLIC_BASE_URL` must
  name the same origin the registry uses to reach the specialist (a proxy that publishes a different public
  origin needs the registry to use it too).
- The client caches each specialist's card for five minutes.

## Testing

`agents/python/tests/test_a2a_conformance.py` drives a real specialist app (real MAF agent, tool loop, step
recorder and auth middleware; only the model is scripted) with the official `a2a-sdk` client, over
`ASGITransport` and over a real uvicorn socket: the card, blocking and streamed tasks, lifecycle,
artifact chunking, extensions on and off, model-input equivalence, failures, identity and auth.
`tests/test_a2a_transport.py` covers the calling side and asserts the same call returns the same text, steps,
usage and queue frames over either transport.

```bash
cd agents/python
uv run pytest tests/test_a2a_conformance.py tests/test_a2a_transport.py -v
# The replay gate over the new wire (zero missing fixtures, scores equal to the baselines):
A2A_TRANSPORT=a2a scripts/replay-evals.sh --setup
```

## Not done

.NET (B-13: `MapA2A`, the same executor rule, extension URIs and card, a golden-file contract test, then the
mixed-stack profile with `A2A_TRANSPORT=a2a`), signed identity tokens (B-14), `input-required` for approvals,
push notifications, the HTTP+JSON and gRPC bindings, and the 0.3 compatibility shim.
