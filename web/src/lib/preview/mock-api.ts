/**
 * The mock backend of the static preview (#301).
 *
 * `previewFetch` stands in for `fetch` inside `api.ts` when `NEXT_PUBLIC_PREVIEW=1`. It returns real `Response`
 * objects, so every parser, 401 path and SSE reader in the app runs unchanged. JSON routes read the committed
 * snapshot; `POST /api/chat/stream` replays a recorded answer as SSE frames in the shapes the orchestrator emits
 * (`step`, `delta`, plain text, `node`, `handoff`, `metadata`, `run`, `replay_miss`). Nothing leaves the browser.
 */

import type {
  AgentStep,
  ConversationDetail,
  ConversationSummary,
  OrderDetail,
  ProductDetail,
  ProductSummary,
} from "../api";
import raw from "./preview-data.json";
import { TOUR_LOGIN } from "./config";

export interface RecordedAnswer {
  id: string;
  prompt: string;
  mode: string;
  source: "recorded" | "composed";
  agents_involved: string[];
  steps?: AgentStep[];
  /** A specialist's own text, streamed as `delta` before the orchestrator's answer. */
  preview?: string;
  handoffs?: { from: string; to: string }[];
  nodes?: string[];
  pending_approval?: boolean;
  text: string;
}

interface PreviewData {
  user: { email: string; name: string; role: string };
  tour: { chips: unknown[]; miss_message: string; tour_user: string };
  modes: unknown[];
  graphs: Record<string, string | null>;
  products: ProductSummary[];
  product_details: Record<string, ProductDetail>;
  orders: OrderDetail[];
  answers: Record<string, RecordedAnswer>;
}

export const data = raw as unknown as PreviewData;

/** One SSE frame, and how long to wait before sending it so the turn looks live. */
export interface Frame {
  event: string;
  data: string;
  delayMs: number;
}

const STEP_DELAY = 450;
const TOKEN_DELAY = 22;
const NODE_DELAY = 350;

function normalize(text: string): string {
  return text.replace(/\s+/g, " ").trim().toLowerCase();
}

/** The recording for a prompt, or `undefined` when nobody recorded it. Exact match, whitespace and case aside. */
export function findAnswer(message: string): RecordedAnswer | undefined {
  const wanted = normalize(message);
  return Object.values(data.answers).find((a) => normalize(a.prompt) === wanted);
}

/** Text split into stream-sized pieces that rejoin to exactly the original. */
export function chunkText(text: string, wordsPerChunk = 3): string[] {
  const words = text.match(/\S+\s*|\s+/g) ?? [];
  const out: string[] = [];
  for (let i = 0; i < words.length; i += wordsPerChunk) out.push(words.slice(i, i + wordsPerChunk).join(""));
  return out;
}

const json = (value: unknown): string => JSON.stringify(value);

/** The frames of one turn, in the order the orchestrator sends them. Pure: no timers, no I/O. */
export function buildChatFrames(message: string, conversationId: string): Frame[] {
  const answer = findAnswer(message);
  if (!answer) {
    return [
      { event: "replay_miss", data: json({ message: data.tour.miss_message }), delayMs: 250 },
      { event: "metadata", data: json({ conversation_id: conversationId, agents_involved: [] }), delayMs: 0 },
      { event: "", data: "[DONE]", delayMs: 0 },
    ];
  }

  const frames: Frame[] = [];
  for (const node of answer.nodes ?? []) {
    frames.push({ event: "node", data: json({ node_id: node, phase: "enter", agent: null, payload: {} }), delayMs: NODE_DELAY });
    frames.push({ event: "node", data: json({ node_id: node, phase: "exit", agent: null, payload: {} }), delayMs: NODE_DELAY });
  }
  for (const h of answer.handoffs ?? []) {
    frames.push({ event: "handoff", data: json({ node_id: h.to, from: h.from, to: h.to }), delayMs: NODE_DELAY });
  }
  for (const step of answer.steps ?? []) {
    frames.push({ event: "step", data: json(step), delayMs: STEP_DELAY });
  }
  for (const piece of answer.preview ? chunkText(answer.preview, 4) : []) {
    frames.push({ event: "delta", data: piece, delayMs: TOKEN_DELAY });
  }
  for (const piece of chunkText(answer.text)) {
    frames.push({ event: "", data: piece, delayMs: TOKEN_DELAY });
  }
  frames.push({
    event: "metadata",
    data: json({ conversation_id: conversationId, agents_involved: answer.agents_involved }),
    delayMs: 0,
  });
  if (answer.pending_approval) {
    frames.push({ event: "run", data: json({ run_id: `preview-run-${answer.id}`, pending_approval: true }), delayMs: 0 });
  }
  frames.push({ event: "", data: "[DONE]", delayMs: 0 });
  return frames;
}

/** A frame in SSE wire form. Multi-line data becomes one `data:` line per line, as the client rejoins them. */
export function encodeFrame(frame: Frame): string {
  const head = frame.event ? `event: ${frame.event}\n` : "";
  const body = frame.data
    .split("\n")
    .map((line) => `data: ${line}`)
    .join("\n");
  return `${head}${body}\n\n`;
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve) => {
    if (ms <= 0 || signal?.aborted) return resolve();
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => (clearTimeout(t), resolve()), { once: true });
  });

function sseResponse(frames: Frame[], signal?: AbortSignal | null): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const frame of frames) {
        await sleep(frame.delayMs, signal ?? undefined);
        if (signal?.aborted) break;
        controller.enqueue(encoder.encode(encodeFrame(frame)));
      }
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

// ---------------------------------------------------------------------------
// JSON routes
// ---------------------------------------------------------------------------

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const readOnly = () =>
  jsonResponse({ detail: "This preview is read-only: it replays recorded answers. Run the stack locally to change data." }, 403);

const PREVIEW_TOKEN = "preview-token";

interface Conversation extends ConversationDetail {
  message_count: number;
}
const conversations = new Map<string, Conversation>();
let conversationCounter = 0;

function recordTurn(conversationId: string, message: string, agents: string[], reply: string): void {
  const now = new Date().toISOString();
  const conv =
    conversations.get(conversationId) ??
    ({ id: conversationId, title: message.slice(0, 60), created_at: now, last_message_at: now, messages: [], message_count: 0 } as Conversation);
  const base = { agent_name: null, metadata: {}, tokens_in: null, tokens_out: null, created_at: now };
  conv.messages.push(
    { id: `${conversationId}-u${conv.messages.length}`, role: "user", content: message, agents_involved: [], ...base },
    { id: `${conversationId}-a${conv.messages.length}`, role: "assistant", content: reply, agents_involved: agents, ...base },
  );
  conv.message_count = conv.messages.length;
  conv.last_message_at = now;
  conversations.set(conversationId, conv);
}

/** Forget in-memory conversations (tests). */
export function resetPreviewState(): void {
  conversations.clear();
  conversationCounter = 0;
}

function listProducts(params: URLSearchParams): Response {
  const search = (params.get("search") ?? "").toLowerCase();
  const category = params.get("category");
  const min = params.get("min_price");
  const max = params.get("max_price");
  let rows = data.products.filter(
    (p) =>
      (!category || p.category === category) &&
      (!min || p.price >= Number(min)) &&
      (!max || p.price <= Number(max)) &&
      (!search || `${p.name} ${p.brand} ${p.description}`.toLowerCase().includes(search)),
  );
  const sorters: Record<string, (a: ProductSummary, b: ProductSummary) => number> = {
    price_asc: (a, b) => a.price - b.price,
    price_desc: (a, b) => b.price - a.price,
    rating: (a, b) => b.rating - a.rating,
    name: (a, b) => a.name.localeCompare(b.name),
  };
  rows = [...rows].sort(sorters[params.get("sort") ?? "rating"] ?? sorters.rating);
  const categories = [...new Set(data.products.map((p) => p.category))].sort();
  return jsonResponse({ products: rows, total: rows.length, categories });
}

function ordersSummary() {
  return data.orders.map((o) => ({
    id: o.id,
    status: o.status,
    total: o.total,
    carrier: o.carrier,
    tracking: o.tracking,
    item_count: o.items.reduce((n, i) => n + i.quantity, 0),
    date: o.date,
  }));
}

async function route(method: string, url: URL, init: RequestInit): Promise<Response> {
  const path = url.pathname.replace(/\/+$/, "");

  if (method === "POST" && path === "/api/chat/stream") {
    const body = JSON.parse(String(init.body ?? "{}")) as { message?: string; conversation_id?: string };
    const message = body.message ?? "";
    const conversationId = body.conversation_id || `preview-conv-${++conversationCounter}`;
    const frames = buildChatFrames(message, conversationId);
    const answer = findAnswer(message);
    if (answer) recordTurn(conversationId, message, answer.agents_involved, answer.text);
    return sseResponse(frames, init.signal);
  }
  if (method === "POST" && path === "/api/auth/login") {
    const body = JSON.parse(String(init.body ?? "{}")) as { email?: string; password?: string };
    if (body.email?.toLowerCase() !== TOUR_LOGIN.email || body.password !== TOUR_LOGIN.password) {
      return jsonResponse({ detail: `The preview has one account: sign in as ${TOUR_LOGIN.email} (password ${TOUR_LOGIN.password}).` }, 400);
    }
    return jsonResponse({ access_token: PREVIEW_TOKEN, refresh_token: PREVIEW_TOKEN, user: data.user });
  }
  if (method === "POST" && path === "/api/auth/refresh") {
    return jsonResponse({ access_token: PREVIEW_TOKEN, refresh_token: PREVIEW_TOKEN });
  }
  if (method !== "GET") return readOnly();

  if (path === "/api/demo/tour") return jsonResponse(data.tour);
  if (path === "/api/orchestration/modes") return jsonResponse(data.modes);
  const graph = path.match(/^\/api\/orchestration\/modes\/(.+)\/graph$/);
  if (graph) {
    const name = decodeURIComponent(graph[1]);
    return name in data.graphs ? jsonResponse({ name, mermaid: data.graphs[name] }) : jsonResponse({ detail: "unknown mode" }, 404);
  }
  if (path === "/api/products") return listProducts(url.searchParams);
  const product = path.match(/^\/api\/products\/([^/]+)$/);
  if (product) {
    const detail = data.product_details[product[1]];
    return detail ? jsonResponse(detail) : jsonResponse({ detail: "Product not found" }, 404);
  }
  if (path === "/api/orders") return jsonResponse({ orders: ordersSummary(), total: data.orders.length });
  const order = path.match(/^\/api\/orders\/([^/]+)$/);
  if (order) {
    const found = data.orders.find((o) => o.id === order[1]);
    return found ? jsonResponse(found) : jsonResponse({ detail: "Order not found" }, 404);
  }
  if (path === "/api/conversations") {
    const list: ConversationSummary[] = [...conversations.values()]
      .sort((a, b) => b.last_message_at.localeCompare(a.last_message_at))
      .map(({ id, title, message_count, created_at, last_message_at }) => ({ id, title, message_count, created_at, last_message_at }));
    return jsonResponse(list);
  }
  const conv = path.match(/^\/api\/conversations\/([^/]+)$/);
  if (conv) {
    const found = conversations.get(conv[1]);
    return found ? jsonResponse(found) : jsonResponse({ detail: "Conversation not found" }, 404);
  }
  if (path === "/api/cart") {
    return jsonResponse({
      id: "preview-cart", items: [], item_count: 0, subtotal: 0, discount_amount: 0, coupon_code: null, total: 0,
      shipping_address: null, billing_address: null, billing_same_as_shipping: true,
    });
  }
  if (path === "/api/profile") {
    return jsonResponse({
      id: "preview-user", ...data.user, loyalty_tier: "silver", total_spend: 0, member_since: "2026-01-01",
      order_count: data.orders.length, review_count: 0,
      tier_benefits: { discount_pct: 5, free_shipping_threshold: null, priority_support: false },
    });
  }
  if (path === "/api/me/notifications") return jsonResponse({ notifications: [], unread_count: 0 });
  if (path === "/api/marketplace/agents" || path === "/api/marketplace/my-agents") return jsonResponse([]);
  if (path === "/api/user/memories") return jsonResponse({ memories: [] });
  return jsonResponse({ detail: "Not available in the preview." }, 404);
}

/** `fetch` for the preview build. Same signature, no network. */
export async function previewFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const href = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  const url = new URL(href, "http://preview.invalid");
  return route((init.method ?? "GET").toUpperCase(), url, init);
}
