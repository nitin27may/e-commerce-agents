# Frontend Architecture

How the web app in [`web/`](../web) is put together: routes, theming, the chat
stream, the orchestration UI and the tests. It is for a developer changing the
frontend or wiring a client to the same API.

Next.js 16 (App Router) · React 19 · Tailwind CSS 4 · shadcn/ui · framer-motion ·
recharts. Source in [`web/`](../web). All backend calls go through the typed
client `web/src/lib/api.ts`.

> Next.js 16 has breaking changes vs older docs — read `node_modules/next/dist/docs/`
> before editing framework code.

## Screenshots

<table>
<tr>
  <td><img src="images/shop-ai-assistant.png" alt="AI shopping assistant with product cards" width="400"/></td>
  <td><img src="images/agent-timeline.png" alt="Live agent activity timeline" width="400"/></td>
</tr>
<tr>
  <td align="center"><em>AI shopping assistant — product cards from Product Discovery agent</em></td>
  <td align="center"><em>Agent activity timeline — live orchestrator → specialist → tool trace</em></td>
</tr>
</table>

## Audiences & routing

Two front doors, by audience:

| Area | Routes | Auth | Notes |
|---|---|---|---|
| **Project landing** | `/` | public | OSS showcase page (architecture, agents, stack). CTA "Try the demo" → `/shop`. |
| **Public storefront** | `/shop`, `/shop/products`, `/shop/products/[id]`, `/shop/assistant` | public | Browse, search, and discovery chat with **no login**. |
| **Account console** | `(app)/` → `/home`, `/chat`, `/orders`, `/orders/[id]`, `/checkout`, `/profile`, `/admin/*`, `/seller/*` | required | Sidebar shell; `(app)/layout.tsx` redirects anonymous users to `/login`. |
| **Auth** | `/login`, `/signup` | public | Self-contained JWT. |

The storefront is **public because the backend serves product browse + chat
anonymously** (`optional_auth` on `/api/products` and `/api/chat*`). Account
actions (cart checkout, orders, tracking, returns) require sign-in.

## Design system & theming

- OKLCH tokens in `web/src/app/globals.css` (`--background`, `--foreground`,
  `--card`, `--primary`, `--muted`, `--chart-1..5`, sidebar tokens). **Use tokens,
  not hardcoded slate/white** — that breaks dark mode.
- Dark mode = a `.dark` class on `<html>`. `ThemeToggle`
  (`components/ui/theme-toggle.tsx`) flips it via `useSyncExternalStore`; a no-flash
  init script in the root layout applies the persisted/system theme before paint.
- Motion variants in `web/src/lib/motion.ts` (reduced-motion aware).
- Primitives: `StatCard`, `SectionHeader`, `Skeleton`, `ChartContainer`
  (recharts wrapper), command palette (Cmd-K).

## Chat / SSE streaming + agent timeline

`api.chatStream(message, conversationId, onChunk, signal?, { onStep })` consumes
the orchestrator SSE stream `POST /api/chat/stream`. Frame types:

- `data: <text>` — streamed answer tokens (`onChunk`).
- `event: step` + `data: {AgentStep}` — one tool-call step
  (`{agent, tool_name, tool_input, tool_output, status, duration_ms}`) → `onStep`.
- `event: metadata` + `data: {conversation_id, agents_involved}` — final.
- `data: [DONE]` — terminator.

`AgentTimeline` (`components/chat/agent-timeline.tsx`) renders the steps as a
collapsible "Agent activity" disclosure (orchestrator → specialist → tool). Rich
assistant content (product/order/checkout cards) is parsed by
`components/chat/rich-message.tsx`.

### SSE streaming sequence

```mermaid
sequenceDiagram
    participant UI as Chat UI
    participant API as api.ts chatStream()
    participant ORCH as Orchestrator SSE<br/>POST /api/chat/stream
    participant SPEC as Specialist Agent

    UI->>API: chatStream(message, convId, onChunk, {onStep})
    API->>ORCH: POST /api/chat/stream (JWT Bearer)
    Note over ORCH: Opens SSE response<br/>Content-Type: text/event-stream

    ORCH->>SPEC: A2A /message:send
    SPEC-->>ORCH: Tool result (e.g. product list)

    ORCH-->>API: event: step\ndata: {agent, tool_name, ...}
    API->>UI: onStep(AgentStep) → AgentTimeline renders step

    ORCH-->>API: data: token token token ...
    API->>UI: onChunk(token) → streamed text appears

    ORCH-->>API: event: metadata\ndata: {conversation_id, agents_involved}
    ORCH-->>API: data: [DONE]
    API->>UI: Stream closed
```

## Orchestration modes, live graph and runs

In the chat composer the user picks how the orchestrator should handle the next message: one of
its five orchestration modes. While a workflow mode runs, a diagram of that mode's graph lights up
node by node. A compare dialog sends one prompt through several modes and shows the answers side by
side, and the `/runs` page lists past runs and lets staff approve or reject a paused one.

The chat page is `app/(app)/chat/page.tsx`. The pieces:

- **Mode switcher** (`components/chat/mode-switcher.tsx`). A composer control fed by
  `GET /api/orchestration/modes`. The selection travels as the `mode` option of
  `api.chatStream()` and is remembered per conversation in `localStorage` (a failure to read or
  write it is ignored). If the modes fetch fails or returns nothing, the control hides and chat
  uses the server's default mode.
- **Live orchestration graph** (`components/chat/orchestration-graph.tsx`). Renders a mode's
  `graph_mermaid()` output client-side, applying the house palette in the browser (the backend's
  source carries no styling), and lights nodes as `event: node` / `event: error` frames arrive
  through the `onOrchestrationEvent` hook. Matching a live `node_id` to a diagram node relies on a
  backend convention: every workflow mode uses the real executor id, dashes swapped for
  underscores, as the Mermaid node id. SSE frames outside `step` / `metadata` / `delta` / plain
  text go to that hook rather than into the chat bubble.
- **Mode comparison** (`components/chat/mode-comparison.tsx`). A standalone dialog, not tied to the
  active conversation. It calls `POST /api/orchestration/compare` to run one prompt through
  several modes and shows a result card per mode, reusing the graph in a static rendering by
  passing the mode's `agents_involved` as `doneNodeIds`.
- **Runs and approvals** (`app/(app)/runs/page.tsx`). Lists past runs and fetches
  `GET /api/runs/{id}/checkpoints` for every visible run up front, so a "Needs approval" badge
  shows without first expanding the row. A run with a pending `hitl_request` gets Approve and
  Reject buttons that call `POST /api/orchestration/{run_id}/resume`. Rows are always
  expandable: workflow modes never populate `agent_execution_steps`, so gating on steps would hide
  exactly the runs most likely to be waiting on approval.

## Testing

- Unit/component: **vitest + React Testing Library** (`pnpm test`, jsdom).
- E2E: **Playwright** under `web/e2e/`. `ui-smoke.spec.ts` runs backend-free
  (mocked auth via localStorage + `page.route('**/api/**')`); the other specs
  need the full live stack.
- In CI, the `web-ui-smoke` job in `.github/workflows/tests.yml` runs three backend-free
  specs (`ui-smoke`, `not-found`, `a11y`) against a production build, on the desktop and
  `mobile-chromium` projects. The rest of `web/e2e/` is not run in CI: it needs the live
  stack. Run it locally with `cd web && pnpm exec playwright test`.

## Gotchas

- **Theme tokens, never raw palette classes.** Hard-coded slate or white classes break dark mode; use the OKLCH tokens ([CONTRIBUTING](https://github.com/nitin27may/e-commerce-agents/blob/main/CONTRIBUTING.md)).
- **Next.js 16 differs from older docs.** Read `node_modules/next/dist/docs/` before touching framework code.
- **Playwright: wait on the composer's stop icon, never on status text.** A streamed turn is finished when the submit button stops showing the stop icon; the "Routing to specialists" text disappears mid-turn.
- **Three specs need no backend** (`ui-smoke`, `not-found`, `a11y`) and run on desktop and `mobile-chromium`; the rest of `e2e/` needs the running stack and runs on desktop only.

## Related

- [`docs/architecture.md`](architecture.md) — full system architecture and SSE orchestration pattern
- [`docs/api-reference.md`](api-reference.md) — REST endpoints the frontend calls
- [`docs/troubleshooting.md`](troubleshooting.md) — frontend-specific issues (products not showing, CORS)
- [Project README](../README.md)
