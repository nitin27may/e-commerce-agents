# Web — E-Commerce Agents frontend

Next.js 16 (App Router) + React 19 + Tailwind CSS 4 + shadcn/ui. This is the
public storefront, the agentic chat, and the authenticated account console for
the multi-agent backend.

> **Heads-up:** this is Next.js **16.x** — APIs differ from older docs. Read the
> relevant guide in `node_modules/next/dist/docs/` before changing framework
> code. See [`AGENTS.md`](./AGENTS.md).

## Commands

```bash
pnpm install          # install deps (pnpm, not npm/yarn)
pnpm dev              # dev server on http://localhost:3000
pnpm build            # production build
pnpm lint             # eslint
pnpm test             # vitest (unit/component, jsdom)
pnpm exec playwright test                        # E2E (needs the app running)
pnpm exec playwright test e2e/ui-smoke.spec.ts   # backend-free UI smoke (mocked auth/API)
```

`ORCHESTRATOR_URL` points at the orchestrator (default `http://localhost:8080`). It is
**server-side**, not `NEXT_PUBLIC_*`: the browser only ever calls this app's own origin, and
`src/app/api/[...path]/route.ts` forwards `/api/*` from there. So it is read per request rather
than compiled into the bundle, which is what lets one image run against any backend.

`NEXT_PUBLIC_API_URL` still works as an escape hatch for calling an orchestrator directly, and
brings CORS back with it.

## Layout

- `src/app/` — App Router.
  - `/` project landing; `shop/*` public storefront (home, products, cart,
    assistant); `(app)/*` auth-gated account console (home dashboard, chat,
    orders, checkout, profile, admin, seller); `login`, `signup`.
- `src/components/` — `ui/` (shadcn + primitives: StatCard, Chart, Skeleton,
  ThemeToggle, command-palette), `chat/` (RichMessage, product/order cards,
  AgentTimeline), `shop/`, `landing/`, `home/`, `sidebar`, `top-bar`.
- `src/lib/` — `api.ts` (typed client + SSE `chatStream`), `auth-context`,
  `cart-context`, `nav.ts`, `motion.ts`, `scenarios.ts`, `format`, `images`.

See [`../docs/frontend.md`](../docs/frontend.md) for routes, theming, the SSE
streaming/timeline contract, and the public-vs-authenticated model.
