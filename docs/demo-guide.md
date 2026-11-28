# Demo Guide

What to click, who to sign in as, and what to ask — for a walkthrough, a
screen recording, or your first ten minutes with the app.

Start the stack first: [Quick Start](quick-start.md). The fast path is
`./scripts/dev.sh --demo`, which pulls prebuilt images rather than building.

## Try it without installing anything

The [static preview](preview.md) (<https://nitinksingh.com/e-commerce-agents/preview/>) is the web UI
replaying the tour's recorded answers in your browser. No stack, no key. It cannot answer anything that was not
recorded; for that, run the stack.

## No-key mode

`./scripts/dev.sh --demo --no-key` (PowerShell: `./scripts/dev.ps1 -Demo -NoKey`) runs the whole stack
on recorded model responses, so it needs no API key. `docker-compose.nokey.yml` hardcodes
`LLM_PROVIDER=replay`, `DEMO_TOUR_ENABLED=true` and the settings that shape prompts, so the recorded
answers always match. The chat shows suggested prompts (the tour); each one has a recorded answer.
A prompt that was not recorded is reported in the chat as a replay miss and offers the suggestions
again. The stack stays up either way.

Sign in as `tour@example.com` / `tour123`: a dedicated account whose orders the tour depends on, with
a delivered order over $500 for the return flow. **Reset demo** in the chat puts it back after a run.
`alice.johnson@gmail.com` / `customer123` also has recordings, for the scenarios listed under Demo Scenarios
below (1 to 8, the handoff and pre-purchase prompts of 9, and a short Allbirds conversation), for the README clips,
and for the prompts the Playwright specs send (the greeting for five accounts, the generative-UI, shopping, follow-up
and grounding prompts); the key-free Playwright run is `E2E_NO_KEY=1 pnpm exec playwright test`, and it reports the
tests that send an unrecorded prompt as skipped. Other accounts and reworded prompts miss.

The recordings are `agents/python/demo/fixtures/replay/`, and `agents/python/demo/fixtures/manifest.json` says which
prompt each one answers. They are fixed answers: a date or a count in one ("21 days left in the return window") is
as of the recording. Two limits. A prompt that changes data (a return, a checkout) changes what a later prompt sees,
so run the tour from a fresh stack (`--clean`) or use **Reset demo**. The same goes for the database's age: the
60-day price window of "is it a good deal?" holds 59 points on a fresh seed and fewer as the seed ages, and the
recording was made on a fresh seed. And the two workflow modes, pre-purchase and
return-and-replace, call no model, so there is nothing recorded for them and nothing to miss.

The GIFs and videos in these docs are recorded on this stack by `scripts/record-media.sh`, driven by
`web/e2e/demo-media.spec.ts`. How to re-record them, and the capture settings, are in
[scripts/demo/README.md](https://github.com/nitin27may/e-commerce-agents/blob/main/scripts/demo/README.md).

## Test Users

Pre-seeded accounts for testing different roles. These credentials belong to the local demo data the seeder creates and nothing else: a deployment to Azure generates its own staff passwords, and none of these should exist outside a throwaway environment.


| Email | Password | Role | Loyalty Tier |
|-------|----------|------|-------------|
| `admin.demo@gmail.com` | admin123 | Admin | Gold |
| `support.demo@gmail.com` | support123 | Support (staff) | Bronze |
| `seller.demo@gmail.com` | seller123 | Seller | Bronze |
| `seller2.demo@gmail.com` | seller123 | Seller | Bronze |
| `alice.johnson@gmail.com` | customer123 | Customer | Gold |
| `bob.smith@gmail.com` | customer123 | Customer | Silver |
| `power.demo@gmail.com` | power123 | Power user (a customer with access to every agent) | Gold |
| `tour@example.com` | tour123 | Customer, for the no-key tour (reset with **Reset demo**) | Silver |

`support` is a staff role: it reviews approvals that policy routes to support (`admin` acts as manager and can do everything support can).

---

## Agent Catalog

| Agent | Port | Description | Key Tools |
|-------|------|-------------|-----------|
| **Customer Support** (Orchestrator) | 8080 | Routes requests to specialists via A2A | `call_specialist_agent` |
| **Product Discovery** | 8081 | Search, semantic search, comparisons, trending | `search_products`, `semantic_search`, `compare_products` |
| **Order Management** | 8082 | Order tracking, cancellation, returns, refunds | `get_user_orders`, `cancel_order`, `initiate_return` |
| **Pricing & Promotions** | 8083 | Coupon validation, cart optimization, loyalty | `validate_coupon`, `optimize_cart`, `get_active_deals` |
| **Review & Sentiment** | 8084 | Sentiment analysis, fake review detection | `analyze_sentiment`, `detect_fake_reviews` |
| **Inventory & Fulfillment** | 8085 | Stock, shipping estimates, fulfillment planning | `check_stock`, `estimate_shipping` |

---

## Demo Scenarios

Try these in the chat after logging in:

1. **Product Search**: "Find me noise-cancelling wireless headphones under $300"
2. **Comparison**: "Compare the Sony WH-1000XM5 with AirPods Max"
3. **Order Tracking**: "Where's my latest order?"
4. **Return Flow**: "I want to return my last order"
5. **Price Check**: "Is the Logitech MX Master 3S a good deal right now?"
6. **Review Analysis**: "What do people think about the Dyson V15?"
7. **Stock Check**: "Is the Dyson V15 Detect in stock?"
8. **Multi-Intent**: "Return my jacket and find me a warmer one under $200"

A coupon works once per user. No seeded order carries a coupon code, so every demo account can still use `WELCOME10`
(the Power User's cart already holds it). After a checkout with a code, applying that code again answers "You have
already used this coupon"; cancel the order to free it, or use another code. `/api/demo/reset` deletes the tour user's orders,
so that account gets its coupons back; other accounts keep theirs used until the order is cancelled.

### See how it works: the Lab

For every role, the storefront chat shows answers and cards, never the machinery. Staff, and developers following the
course, get the same chat with everything shown at **Lab** (`/lab`): sign in as `admin.demo@gmail.com` /
`admin123` or `support.demo@gmail.com` / `support123`.

9. **Change the mode.** In the Lab, open the **Mode** dropdown above the composer and pick **Pre-Purchase
   Research**, then ask "I am thinking of buying the Sony WH-1000XM5. Is it a good time to buy?". The mode's graph
   is drawn with the answer and lights up each node as it runs. Under every answer the Lab also lists the agents
   that took part, each tool call with its timing, and the grounding report.

    <img src="images/orchestration-graph-live.jpg" alt="The Lab in Pre-Purchase Research mode: the fan-out graph drawn under the question, with the fan-out node finished and the three branches running" width="820"/>

    *The graph mid-run. The workflow modes call no model and finish in milliseconds, so the recording spaces out
    the stream's frames (`web/e2e/demo-media.spec.ts`); with a live model the pacing is its latency.*

    <img src="images/agent-timeline.jpg" alt="The Lab after a tool-routed turn: the agent activity list with pricing-promotions and inventory-fulfillment tool calls, each with its timing, and the grounding report" width="820"/>
10. **Compare modes on one prompt.** Click **Compare** beside the dropdown, enter one prompt and select several
    modes. Each gets a result card with its answer, latency, steps, tokens and estimated cost, and the graph it
    took. Measured numbers are in the [orchestration benchmark](orchestration-benchmark.md). On the no-key stack
    only the two workflow modes can answer, because Compare sends each model-backed mode a request of its own and
    no recording matches it; with a key, all five answer.

    <img src="images/compare-dialog.jpg" alt="The Lab's Compare dialog on the no-key stack: pre-purchase research and return-and-replace side by side, each card showing its answer, latency, steps and the graph it took" width="820"/>

### A return that needs a person

11. **Approve a paused return.** Sign in as `tour@example.com` / `tour123`, the account seeded with a delivered
    order over the $500 approval threshold, and click **Return my Dyson (needs approval)**. The run pauses at the
    policy gate, nothing is created, and the chat shows "Awaiting staff review".

    <img src="images/return-awaiting-approval.jpg" alt="Chat showing a return paused at the policy gate with an Awaiting staff review notice" width="820"/>

    Sign in as a reviewer in another browser and open **Approvals**. This return is over half the customer's
    lifetime spend, so policy routes it to a manager: use `admin.demo@gmail.com`. **Show details** says why it was
    routed there, what was asked for and the facts the policy checked. Choose **Approve** or **Reject**. The run
    resumes from its checkpoint and finishes as the customer; a rejected return leaves no return row behind. An
    admin can also decide from **Runs**, where the run is marked "Needs approval".

    <img src="images/approvals-inbox.jpg" alt="Approvals inbox as an admin: a pending return with its details open, showing why it needs review, what was requested and what the policy checked" width="820"/>

    <img src="images/runs-approve.jpg" alt="Runs page as an admin: the paused return expanded, with Approve and Reject buttons, above the tour's other runs" width="820"/>

    A smaller return is routed to support instead: ask as `alice.johnson@gmail.com` to return a specific delivered
    order of about $50 and it appears in `support.demo@gmail.com`'s inbox.
    See [approvals](approvals.md).

---

## Journeys

Each clip is recorded by `scripts/record-media.sh` on the no-key stack, so it is exactly what you get.

<details open>
<summary>Recorded journeys (click to collapse)</summary>

**Shop and check out.** Product cards, a review summary, added to the cart from the chat, then checkout.

<img src="images/hero.gif" alt="A shopper asks for noise-cancelling headphones, asks which has the best reviews, adds the Sony WH-1000XM5 to the cart from the chat, then checks out" width="820"/>

**One question, two specialists.** "Is the Sony WH-1000XM5 a good deal right now, and is it in stock?" Pricing and
promotions works out Alice's savings as a Gold member, inventory and fulfillment finds the nearest warehouse with
stock, and the answer arrives as two cards.

<img src="images/multi.gif" alt="One question answered as a savings card and a stock card" width="820"/>

**A return that needs a person.** Alice gives a reason and confirms the exact return before anything happens; a
manager approves it in the inbox; her order now shows the refund and a label to download and print.

<img src="images/return.gif" alt="A return confirmed in the chat, approved in the approvals inbox, and the order page with a Download return label button" width="820"/>

**Where is my order, and should I buy now.**

<img src="images/order.gif" alt="The latest order answered as an order card" width="820"/>

<img src="images/research.gif" alt="A pre-purchase question answered with reviews, stock, price trend and shipping in one list" width="820"/>

**The Lab.** The same chat with its machinery shown, then two modes compared.

<img src="images/lab.gif" alt="The Lab: agent activity for a tool-routed turn, the pre-purchase graph lighting up, and the Compare dialog" width="820"/>

</details>

## Screens

<details open>
<summary>Screenshots: the public store, the signed-in shopper, and the staff pages (click to collapse)</summary>

### Public (no sign-in)

Anyone can browse the catalog and use the shopping assistant without an account.

<table>
<tr><td><img src="images/storefront.jpg" alt="Public storefront: hero, scenario cards and categories" width="820"/></td></tr>
<tr><td align="center"><em>The public store: try a scenario, or browse by category</em></td></tr>
<tr><td><img src="images/flow-guest-assistant.jpg" alt="Public shopping assistant before the first question" width="820"/></td></tr>
<tr><td align="center"><em>The shopping assistant, no sign-in needed</em></td></tr>
</table>

### Signed in: answers as components

Every answer renders as generative UI, not raw text: the component (card, table, chart, badge) is picked by the
kind of data an agent returns, from one registry (`web/src/lib/chat-schemas.ts`).

<table>
<tr><td><img src="images/flow-product-search.jpg" alt="Chat: product search answered with a product card" width="820"/></td></tr>
<tr><td align="center"><em>Find a product: results as cards with add to cart and details</em></td></tr>
<tr><td><img src="images/flow-add-to-cart.jpg" alt="Chat: the Sony WH-1000XM5 added to the cart, shown as a cart card with a checkout button" width="820"/></td></tr>
<tr><td align="center"><em>Add to cart from the chat: the cart comes back as a card with a checkout button</em></td></tr>
<tr><td><img src="images/flow-order-tracking.jpg" alt="Chat: the latest order as an order card" width="820"/></td></tr>
<tr><td align="center"><em>Track an order: items, totals, carrier, tracking number and address</em></td></tr>
<tr><td><img src="images/flow-review-sentiment.jpg" alt="Chat: review sentiment as a card with a rating distribution and a trend" width="820"/></td></tr>
<tr><td align="center"><em>What buyers say: the rating distribution and how it moved over time</em></td></tr>
<tr><td><img src="images/flow-view-cart.jpg" alt="Cart page with items and totals" width="820"/></td></tr>
<tr><td align="center"><em>The cart page</em></td></tr>
<tr><td><img src="images/marketplace.jpg" alt="Signed-in product catalog with filters and sort" width="820"/></td></tr>
<tr><td align="center"><em>The catalog: search, categories, sort</em></td></tr>
</table>

### Staff and sellers

<table>
<tr><td><img src="images/admin-dashboard.jpg" alt="Admin overview" width="820"/></td></tr>
<tr><td align="center"><em>Admin overview</em></td></tr>
<tr><td><img src="images/seller-dashboard.jpg" alt="Seller dashboard with sales figures and recent orders" width="820"/></td></tr>
<tr><td align="center"><em>Seller dashboard</em></td></tr>
<tr><td><img src="images/runs-approval.jpg" alt="Runs page as an admin with a run marked Needs approval" width="820"/></td></tr>
<tr><td align="center"><em>Runs: every orchestrated request, its steps, and any approval it waits on (staff only)</em></td></tr>
</table>

</details>

---

