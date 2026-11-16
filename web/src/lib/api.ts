/**
 * Base URL for API calls. Empty by default: the browser talks to its own
 * origin and `src/app/api/[...path]/route.ts` forwards to the orchestrator, so
 * nothing about the backend's address is compiled into this bundle. Set
 * NEXT_PUBLIC_API_URL only to bypass that proxy and call an orchestrator
 * directly — it works, and it brings CORS back with it.
 */
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

/**
 * Absolutize a server-issued `/api/...` path for use outside `fetch` — an
 * anchor href or `window.open`, where a relative path would resolve against
 * the current route rather than the origin.
 */
export function apiUrl(path: string): string {
  return path.startsWith("/api") ? `${API_URL}${path}` : path;
}

export interface Address {
  name?: string;
  street: string;
  city: string;
  state: string;
  zip: string;
  country: string;
  phone?: string;
}

export interface CartItem {
  id: string;
  product_id: string;
  name: string;
  brand: string;
  category: string;
  price: number;
  original_price?: number;
  quantity: number;
  subtotal: number;
  image_url?: string;
  in_stock?: boolean;
  available_qty?: number;
}

/** One step in the agentic timeline streamed via `event: step` SSE frames. */
/** Where a step's data came from — shared/agent_observability.py's StepRecorderMiddleware. */
export interface StepProvenance {
  source: string;
  row_ids: string[];
}

export interface AgentStep {
  agent?: string;
  tool_name: string;
  tool_input?: unknown;
  tool_output?: unknown;
  status?: string;
  duration_ms?: number;
  provenance?: StepProvenance;
}

/** One verified/unverified claim inside a GroundingReport (shared/grounding/verifier.py::ClaimVerdict). */
export interface GroundingClaim {
  type: "product" | "order" | "bare_id" | "amount" | "tracking";
  id: string;
  status: "verified" | "price_mismatch" | "not_found" | "unverifiable";
  detail: string | null;
  source: "ledger" | "db" | null;
}

/**
 * Server-side grounding verdict for one assistant message — how many of its
 * product/order card claims were checked against Postgres, not just
 * format-validated (shared/grounding/middleware.py::_attach_report). Present
 * only when `GROUNDING_MODE` is `annotate` or `enforce` (default: annotate).
 */
export interface GroundingReport {
  total: number;
  verified: number;
  unverified: number;
  claims: GroundingClaim[];
}

/** What a mode supports — from `GET /api/orchestration/modes` (orchestrator/modes/base.py::ModeCapabilities). */
export interface OrchestrationModeCapabilities {
  streams: boolean;
  supports_hitl: boolean;
  supports_checkpoints: boolean;
  is_graph: boolean;
}

/** One entry in `GET /api/orchestration/modes` — a mode `/api/chat`'s `mode` field can select. */
export interface OrchestrationMode {
  name: string;
  label: string;
  description: string;
  capabilities: OrchestrationModeCapabilities;
  default: boolean;
}

/** One suggested prompt in the demo tour — `GET /api/demo/tour` (orchestrator/routes/demo.py). */
export interface DemoTourChip {
  id: string;
  label: string;
  prompt: string;
  /** An `OrchestrationMode.name`. */
  mode: string;
  /** false = continue the previous chip's conversation (a scripted follow-up). */
  new_conversation: boolean;
}

/** The demo tour manifest. Only a backend started as a demo serves one. */
export interface DemoTour {
  chips: DemoTourChip[];
  /** Shown when a prompt has no recorded response. */
  miss_message: string;
  /** The seeded account the tour runs as; "Reset demo" is offered to it alone. */
  tour_user: string;
}

/** One mode's result from `POST /api/orchestration/compare`. */
export interface CompareModeResult {
  mode: string;
  label: string;
  text: string;
  latency_ms: number;
  agents_involved: string[];
  step_count: number;
  graph_mermaid: string | null;
  /** null = the mode reported no usage; 0 = it made no LLM call (the workflow modes). */
  tokens_in?: number | null;
  tokens_out?: number | null;
  est_cost_usd?: number | null;
  error: string | null;
}

export interface CompareResponse {
  message: string;
  results: CompareModeResult[];
}

export interface RunStep {
  step_index: number;
  tool_name: string;
  tool_input: Record<string, unknown> | null;
  tool_output: Record<string, unknown> | null;
  status: string;
  duration_ms: number;
}

export interface AppNotification {
  id: string;
  kind: string;
  title: string;
  body: string;
  link: string | null;
  approval_id: string | null;
  created_at: string;
  read: boolean;
}

/** One suggested change to one product. Money and quantities arrive as numbers (the backend computes in Decimal). */
export interface ProposalLine {
  id: string;
  product_id: string;
  product_name: string;
  field: "price" | "restock_quantity";
  current_value: number | null;
  proposed_value: number;
  final_value: number | null;
  rationale: string | null;
  evidence: Record<string, unknown>;
  /** The guard verdict from when it was proposed; `reasons` are fixed codes such as `margin_below_floor`. */
  guard: { ok: boolean; reasons: string[]; numbers: Record<string, string | null>; policy_version: string } | null;
  status: "proposed" | "accepted" | "edited" | "rejected" | "blocked" | "executed" | "failed";
  executed_at: string | null;
}

export interface SellerProposal {
  id: string;
  seller_email: string;
  kind: "markdown" | "restock" | "mixed";
  trigger: "manual" | "scheduled" | "event";
  status: "pending_review" | "decided" | "executed" | "cancelled";
  created_at: string;
  updated_at: string;
  approval_id: string | null;
  approval_status: string | null;
  due_at: string | null;
  lines: ProposalLine[];
}

export type ProposalLineDecision =
  | { line_id: string; decision: "accept" | "reject" }
  | { line_id: string; decision: "edit"; value: string };

export interface ProposalGenerateResult {
  proposal_id: string | null;
  approval_id: string | null;
  kind: string | null;
  lines: number;
  blocked: number;
  skipped_existing: number;
  skipped_over_batch: number;
}

export interface NotificationsResponse {
  notifications: AppNotification[];
  unread_count: number;
}

export interface RunEntry {
  id: string;
  agent_name: string;
  user_email: string | null;
  user_name: string | null;
  input_summary: string | null;
  tokens_in: number;
  tokens_out: number;
  tool_calls_count: number;
  duration_ms: number;
  status: "success" | "error";
  trace_id: string | null;
  created_at: string;
  steps: RunStep[];
}

export interface CartResponse {
  id: string;
  items: CartItem[];
  item_count: number;
  subtotal: number;
  discount_amount: number;
  coupon_code: string | null;
  total: number;
  shipping_address: Address | null;
  billing_address: Address | null;
  billing_same_as_shipping: boolean;
}

const STORAGE_ACCESS = "ecommerce_access_token";
const STORAGE_REFRESH = "ecommerce_refresh_token";

// localStorage can be missing or throw (SSR, private windows, blocked storage).
function readStored(key: string): string | null {
  try {
    return typeof window === "undefined" ? null : localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string) {
  try {
    if (typeof window !== "undefined") localStorage.setItem(key, value);
  } catch {
    /* in-memory copy still works for this tab */
  }
}

// Stale JWT → clear auth and bounce to /login. Idempotent.
function handleUnauthorized() {
  if (typeof window === "undefined") return;
  localStorage.removeItem("ecommerce_user");
  localStorage.removeItem("ecommerce_access_token");
  localStorage.removeItem("ecommerce_refresh_token");
  if (!window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
}

/**
 * A refresh that failed for a reason that says nothing about the session: the
 * limiter said 429, the server errored (5xx), or the network dropped. The user is
 * still signed in, so the caller keeps the session and surfaces this instead of
 * bouncing them to /login. `retryAfterSeconds` echoes `Retry-After` when sent.
 */
export class RefreshUnavailableError extends Error {
  readonly retryAfterSeconds: number | null;
  constructor(retryAfterSeconds: number | null) {
    super(
      retryAfterSeconds
        ? `Could not refresh your session right now — please try again in about ${retryAfterSeconds} seconds.`
        : "Could not refresh your session right now — please try again shortly."
    );
    this.name = "RefreshUnavailableError";
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

// `Retry-After` is either delta-seconds or an HTTP date; only the former is honoured.
function retryAfterSeconds(res: Response): number | null {
  const raw = res.headers.get("Retry-After");
  if (!raw) return null;
  const n = Number(raw.trim());
  return Number.isFinite(n) && n > 0 ? Math.ceil(n) : null;
}

class ApiClient {
  private token: string | null = null;
  private refreshToken: string | null = null;
  // Single in-flight refresh — rapid concurrent 401s share one network call.
  private inflightRefresh: Promise<string | null> | null = null;

  setToken(token: string | null) {
    this.token = token;
  }

  getToken() {
    return this.token;
  }

  setRefreshToken(token: string | null) {
    this.refreshToken = token;
  }

  /**
   * Attempt to swap the current refresh_token for a fresh access token.
   * Returns the new access token, or `null` if the session is genuinely over
   * (no refresh token, or the server answered 401/403) — the caller should then
   * bounce the user to /login. Throws `RefreshUnavailableError` for a 429, a 5xx
   * or a network failure: those say nothing about the session, which is kept.
   */
  private async tryRefresh(): Promise<string | null> {
    if (!this.refreshToken && !readStored(STORAGE_REFRESH)) return null;
    if (this.inflightRefresh) return this.inflightRefresh;
    // The server rotates refresh tokens: each one is single-use, and presenting
    // a spent one revokes the whole session. So at most ONE refresh may be in
    // flight — per tab (the promise below) and across tabs (a Web Lock, with a
    // re-read of localStorage inside it so the second tab sends the first
    // tab's successor rather than the token it loaded at page start).
    const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
    this.inflightRefresh = (async () => {
      try {
        if (!locks) return await this.refreshOnce();
        return await locks.request("ecommerce-refresh", () => this.refreshOnce());
      } finally {
        this.inflightRefresh = null;
      }
    })();
    return this.inflightRefresh;
  }

  private async refreshOnce(): Promise<string | null> {
    // localStorage is the source of truth: another tab may have rotated since
    // this one last looked.
    const stored = readStored(STORAGE_REFRESH);
    if (stored) this.refreshToken = stored;
    const sent = this.refreshToken;
    if (!sent) return null;
    try {
      const res = await fetch(`${API_URL}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: sent }),
      });
      if (!res.ok) {
        // Another tab may have rotated meanwhile; that is a live session whatever we were told.
        const adopted = this.adoptOtherTabsSession(sent);
        if (adopted) return adopted;
        // Only the server saying the token is bad (or forbidden) ends the session.
        if (res.status === 401 || res.status === 403) return null;
        throw new RefreshUnavailableError(retryAfterSeconds(res));
      }
      const data = (await res.json()) as { access_token?: string; refresh_token?: string };
      if (!data.access_token) return null;
      this.token = data.access_token;
      writeStored(STORAGE_ACCESS, data.access_token);
      // `refresh_token` is absent when the server has rotation switched off
      // (and in AUTH_MODE=oauth): keep using the one we have.
      if (data.refresh_token) {
        this.refreshToken = data.refresh_token;
        writeStored(STORAGE_REFRESH, data.refresh_token);
      }
      return data.access_token;
    } catch (err) {
      if (err instanceof RefreshUnavailableError) throw err;
      // fetch() itself failed (offline, DNS, reset) or the body was unreadable: not a verdict on the session.
      throw new RefreshUnavailableError(null);
    }
  }

  /**
   * A refresh rejected with a token another tab has since replaced is not a
   * dead session — that tab already rotated and stored the successor (and a
   * fresh access token). Adopt them rather than logging the user out.
   */
  private adoptOtherTabsSession(sent: string): string | null {
    const latest = readStored(STORAGE_REFRESH);
    const access = readStored(STORAGE_ACCESS);
    if (!latest || latest === sent || !access) return null;
    this.refreshToken = latest;
    this.token = access;
    return access;
  }

  private async request<T>(
    path: string,
    options: RequestInit = {},
    { allowRefresh = true }: { allowRefresh?: boolean } = {}
  ): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    };
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }

    const res = await fetch(`${API_URL}${path}`, { ...options, headers });

    if (res.status === 401) {
      // One retry: if we have a refresh token, swap it for a fresh
      // access token and replay the request once. Avoids the silent
      // session-death audit finding on long chat sessions.
      if (allowRefresh) {
        const fresh = await this.tryRefresh();
        if (fresh) {
          return this.request<T>(path, options, { allowRefresh: false });
        }
      }
      this.token = null;
      handleUnauthorized();
      throw new Error("Session expired — please log in again.");
    }

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || body.error || `API error ${res.status}`);
    }

    return res.json();
  }

  // Auth
  signup(email: string, password: string, name: string) {
    return this.request<{
      access_token: string;
      refresh_token: string;
      user: { email: string; name: string; role: string };
    }>("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password, name }),
    });
  }

  login(email: string, password: string) {
    return this.request<{
      access_token: string;
      refresh_token: string;
      user: { email: string; name: string; role: string };
    }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  }

  refresh(refreshToken: string) {
    return this.request<{ access_token: string; refresh_token?: string }>("/api/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  }

  // Chat
  chat(message: string, conversationId?: string, signal?: AbortSignal) {
    return this.request<{
      response: string;
      conversation_id: string;
      agents_involved: string[];
      message_id?: string;
      grounding?: GroundingReport | null;
    }>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, conversation_id: conversationId }),
      signal,
    });
  }

  /**
   * Streaming chat — reads SSE events and calls onChunk for each text delta.
   * Returns conversation metadata once the stream completes.
   *
   * Pass an `AbortSignal` to cancel mid-stream (e.g. user navigates away
   * or hits "Stop"). On a 401 the client refreshes once and retries the
   * stream; if the refresh fails the user is bounced to /login.
   */
  async chatStream(
    message: string,
    conversationId: string | undefined,
    onChunk: (text: string) => void,
    signal?: AbortSignal,
    options: {
      allowRefresh?: boolean;
      onStep?: (step: AgentStep) => void;
      /**
       * Fired for any SSE frame that isn't `step`/`metadata`/display text —
       * currently `node`, `handoff`, `checkpoint`, `request_info`, `error`
       * from a non-"tool" orchestration mode (see `orchestrator/routes/chat.py`),
       * plus `run` — `{run_id, pending_approval}`, emitted by every mode after
       * persistence, since the run's id is the `usage_logs` row created there
       * and so cannot be known when `metadata` goes out. Both stacks emit it.
       */
      onOrchestrationEvent?: (eventName: string, data: unknown) => void;
      /**
       * Fired once per `event: grounding` frame — currently only "tool" mode
       * emits it (see `orchestrator/modes/tool_router.py` and
       * `orchestrator/routes/chat.py`'s streaming generator).
       */
      onGrounding?: (report: GroundingReport) => void;
      /**
       * Fired for `event: delta` frames — a specialist's own live-streamed
       * text, forwarded in real time during a "tool" mode turn's
       * `call_specialist_agent` tool call, so the user sees a continuous
       * stream instead of silence during the tool-call gap. This is a
       * *preview*, not the final answer: the orchestrator's own separately-
       * composed final text (delivered via `onChunk`) restates the same
       * content afterward, and the backend never persists delta text into
       * the saved message (Phase 8.1) — kept as a distinct callback rather
       * than folded into `onChunk` so callers can render it as a
       * replaceable preview instead of accumulating it into the same
       * buffer the final answer also writes to, which is what caused the
       * visible duplication this callback's separation fixes.
       */
      onDeltaChunk?: (text: string) => void;
      /**
       * Orchestration mode to run this turn through — a `name` from
       * `GET /api/orchestration/modes`. Omitted (or `undefined`) lets the
       * backend fall back to `settings.ORCHESTRATION_MODE` (default `"tool"`).
       */
      mode?: string;
      /**
       * Fired for `event: replay_miss` — the demo replays recorded model
       * responses and this prompt has none (orchestrator/routes/chat.py). It is
       * a notice for the thread, never display text, so it must not reach
       * `onChunk` or `onOrchestrationEvent`. `message` is the tour manifest's
       * `miss_message`; the stream carries nothing else about the miss.
       */
      onReplayMiss?: (miss: { message: string }) => void;
    } = {}
  ): Promise<{ conversation_id: string; agents_involved: string[] }> {
    const allowRefresh = options.allowRefresh ?? true;
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }

    const res = await fetch(`${API_URL}/api/chat/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        message,
        conversation_id: conversationId,
        mode: options.mode,
      }),
      signal,
    });

    if (res.status === 401) {
      if (allowRefresh) {
        const fresh = await this.tryRefresh();
        if (fresh) {
          return this.chatStream(message, conversationId, onChunk, signal, {
            ...options,
            allowRefresh: false,
          });
        }
      }
      this.token = null;
      handleUnauthorized();
      throw new Error("Session expired — please log in again.");
    }

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || body.error || `API error ${res.status}`);
    }

    const reader = res.body?.getReader();
    if (!reader) {
      throw new Error("ReadableStream not supported");
    }

    const decoder = new TextDecoder();
    let metadata: { conversation_id: string; agents_involved: string[] } | null = null;
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Split on double-newline to get complete SSE events.
        // Splitting on "\n" alone loses newlines embedded in data payloads
        // (e.g. the \n between a ```product fence and its JSON body), because
        // each \n-split line is treated independently and empty data: lines
        // (from a lone-\n token) are silently dropped. Per SSE spec, an event
        // is terminated by \n\n; multiple data: lines within one event must be
        // joined with \n to reconstruct the original value.
        const events = buffer.split("\n\n");
        // Last entry is an incomplete event — keep it in the buffer
        buffer = events.pop() ?? "";

        for (const event of events) {
          const lines = event.split("\n");
          let currentEventType = "";
          const dataParts: string[] = [];

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEventType = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              dataParts.push(line.slice(6));
            }
          }

          if (dataParts.length === 0) continue;

          // Rejoin multi-line data fields with \n (SSE spec §9.2.6)
          const data = dataParts.join("\n");

          if (data === "[DONE]") continue;

          if (currentEventType === "step") {
            try {
              options.onStep?.(JSON.parse(data) as AgentStep);
            } catch {
              // Ignore malformed step
            }
            continue;
          }

          if (currentEventType === "metadata") {
            try {
              metadata = JSON.parse(data);
            } catch {
              // Ignore malformed metadata
            }
            continue;
          }

          if (currentEventType === "replay_miss") {
            try {
              const miss = JSON.parse(data) as { message?: unknown };
              options.onReplayMiss?.({ message: typeof miss.message === "string" ? miss.message : "" });
            } catch {
              options.onReplayMiss?.({ message: "" });
            }
            continue;
          }

          if (currentEventType === "grounding") {
            try {
              options.onGrounding?.(JSON.parse(data) as GroundingReport);
            } catch {
              // Ignore malformed grounding report
            }
            continue;
          }

          // "" (plain `data:` frame) is the orchestrator's own final text —
          // the persisted answer. "delta" (a specialist's live-streamed
          // token) is a preview only, routed to its own callback so callers
          // can treat it as replaceable rather than accumulating it into
          // the same buffer as the final answer (see onDeltaChunk's doc).
          if (currentEventType === "") {
            onChunk(data);
            continue;
          }
          if (currentEventType === "delta") {
            options.onDeltaChunk?.(data);
            continue;
          }

          // Any other named frame (e.g. `node`/`handoff`/`checkpoint`/
          // `request_info`/`error` from a non-"tool" orchestration mode) is
          // structured data, not display text — forward it to the optional
          // hook rather than falling through to onChunk, which would render
          // its raw JSON payload as if it were part of the chat message.
          try {
            options.onOrchestrationEvent?.(currentEventType, JSON.parse(data));
          } catch {
            // Ignore malformed frame
          }
        }
      }
    } catch (err) {
      // AbortError on user-initiated cancel: don't throw, just stop.
      if (err instanceof DOMException && err.name === "AbortError") {
        return metadata ?? { conversation_id: conversationId ?? "", agents_involved: [] };
      }
      throw err;
    } finally {
      reader.releaseLock();
    }

    return metadata ?? { conversation_id: conversationId ?? "", agents_involved: [] };
  }

  // Demo tour (a no-key replay demo; absent on a normal install)

  /**
   * The tour manifest, or `null` when this backend is not a demo. "Not a demo"
   * is detected here, at runtime, from the 404 the route answers with while
   * `DEMO_TOUR_ENABLED` is off — so the build encodes nothing about the backend.
   * Any other failure is also `null`: the tour is an extra, and a flaky fetch
   * must not put an error where the chat's empty state is.
   */
  async getDemoTour(): Promise<DemoTour | null> {
    try {
      const res = await fetch(`${API_URL}/api/demo/tour`);
      if (!res.ok) return null;
      const data = (await res.json()) as Partial<DemoTour>;
      if (!Array.isArray(data.chips) || data.chips.length === 0) return null;
      return {
        chips: data.chips,
        miss_message: typeof data.miss_message === "string" ? data.miss_message : "",
        tour_user: typeof data.tour_user === "string" ? data.tour_user : "",
      };
    } catch {
      return null;
    }
  }

  /** Put the tour user's data back as seeded. The server refuses anyone else. */
  resetDemo() {
    return this.request<{ status: string }>("/api/demo/reset", { method: "POST" });
  }

  // Orchestration modes
  getOrchestrationModes() {
    return this.request<OrchestrationMode[]>("/api/orchestration/modes");
  }

  getModeGraph(name: string) {
    return this.request<{ name: string; mermaid: string | null }>(
      `/api/orchestration/modes/${encodeURIComponent(name)}/graph`,
    );
  }

  compareModes(message: string, modes: string[]) {
    return this.request<CompareResponse>("/api/orchestration/compare", {
      method: "POST",
      body: JSON.stringify({ message, modes }),
    });
  }

  // Conversations
  getConversations() {
    return this.request<any[]>("/api/conversations");
  }

  getConversation(id: string) {
    return this.request<any>(`/api/conversations/${id}`);
  }

  deleteConversation(id: string) {
    return this.request<any>(`/api/conversations/${id}`, { method: "DELETE" });
  }

  // Marketplace
  getAgentCatalog() {
    return this.request<any[]>("/api/marketplace/agents");
  }

  requestAccess(agentName: string, roleRequested: string, useCase: string) {
    return this.request<any>("/api/marketplace/request", {
      method: "POST",
      body: JSON.stringify({
        agent_name: agentName,
        role_requested: roleRequested,
        use_case: useCase,
      }),
    });
  }

  getMyAgents() {
    return this.request<any[]>("/api/marketplace/my-agents");
  }

  // Admin
  getAccessRequests() {
    return this.request<any[]>("/api/admin/requests");
  }

  approveRequest(id: string, notes?: string) {
    return this.request<any>(`/api/admin/requests/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ admin_notes: notes }),
    });
  }

  denyRequest(id: string, notes?: string) {
    return this.request<any>(`/api/admin/requests/${id}/deny`, {
      method: "POST",
      body: JSON.stringify({ admin_notes: notes }),
    });
  }

  getAgentStats() {
    return this.request<
      { agent_name: string; request_count: number; avg_duration_ms: number; total_tokens: number }[]
    >("/api/agents/stats");
  }

  // HITL
  getHitlRequests(status?: string) {
    const q = status ? `?status=${encodeURIComponent(status)}` : "";
    return this.request<{
      requests: {
        id: string;
        user_email: string;
        agent_name: string;
        tool_name: string;
        tool_input: Record<string, unknown>;
        status: string;
        admin_note: string | null;
        approved_by: string | null;
        execution_result: Record<string, unknown> | null;
        created_at: string;
        resolved_at: string | null;
      }[];
      total: number;
    }>(`/api/admin/hitl/requests${q}`);
  }

  approveHitlRequest(id: string, note?: string) {
    return this.request<{ status: string; execution_result: Record<string, unknown> }>(
      `/api/admin/hitl/requests/${id}/approve`,
      { method: "POST", body: JSON.stringify({ note: note ?? null }) },
    );
  }

  denyHitlRequest(id: string, note?: string) {
    return this.request<{ status: string }>(
      `/api/admin/hitl/requests/${id}/deny`,
      { method: "POST", body: JSON.stringify({ note: note ?? null }) },
    );
  }

  getUsageStats() {
    return this.request<any>("/api/admin/usage");
  }

  getNotifications(limit = 20) {
    return this.request<NotificationsResponse>(`/api/me/notifications?limit=${limit}`);
  }

  markNotificationRead(id: string) {
    return this.request<{ ok: boolean }>(
      `/api/me/notifications/${encodeURIComponent(id)}/read`,
      { method: "POST" },
    );
  }

  getRuns(params?: { limit?: number; offset?: number }) {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));
    const qs = q.toString();
    return this.request<{
      entries: RunEntry[];
      total: number;
      limit: number;
      offset: number;
    }>(`/api/runs${qs ? `?${qs}` : ""}`);
  }

  getRunCheckpoints(runId: string) {
    return this.request<{
      run_id: string;
      checkpoints: { checkpoint_id: string; workflow_name: string; created_at: string }[];
      hitl_request: {
        id: string;
        status: "pending" | "approved" | "rejected" | "timeout";
        payload: Record<string, unknown>;
        response: Record<string, unknown> | null;
        created_at: string;
        responded_at: string | null;
      } | null;
    }>(`/api/runs/${runId}/checkpoints`);
  }

  resumeRun(runId: string, approved: boolean) {
    return this.request<{ run_id: string; approved: boolean; text: string; agents_involved: string[] }>(
      `/api/orchestration/${runId}/resume`,
      { method: "POST", body: JSON.stringify({ approved }) },
    );
  }

  getAuditLog(params?: {
    limit?: number;
    offset?: number;
    agent_name?: string;
    status?: string;
    search?: string;
  }) {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));
    if (params?.agent_name) q.set("agent_name", params.agent_name);
    if (params?.status) q.set("status", params.status);
    if (params?.search) q.set("search", params.search);
    const qs = q.toString();
    return this.request<{
      entries: {
        id: string;
        agent_name: string;
        user_email: string | null;
        user_name: string | null;
        input_summary: string | null;
        tokens_in: number;
        tokens_out: number;
        tool_calls_count: number;
        duration_ms: number;
        status: "success" | "error";
        error_message: string | null;
        trace_id: string | null;
        created_at: string;
        steps: {
          step_index: number;
          tool_name: string;
          tool_input: Record<string, unknown> | null;
          tool_output: Record<string, unknown> | null;
          status: string;
          duration_ms: number;
        }[];
      }[];
      total: number;
      limit: number;
      offset: number;
    }>(`/api/admin/audit${qs ? `?${qs}` : ""}`);
  }

  // Seller
  getSellerProducts() {
    return this.request<{ products: any[]; total: number }>("/api/seller/products");
  }

  getSellerOrders() {
    return this.request<{ orders: any[]; total: number }>("/api/seller/orders");
  }

  getSellerStats() {
    return this.request<any>("/api/seller/stats");
  }

  // Seller merchant proposals (#93)
  getSellerProposals() {
    return this.request<{ proposals: SellerProposal[]; total: number }>("/api/seller/proposals");
  }

  runSellerProposals() {
    return this.request<ProposalGenerateResult>("/api/seller/proposals/run", { method: "POST", body: "{}" });
  }

  decideSellerProposal(id: string, lines: ProposalLineDecision[], note?: string) {
    return this.request<{
      proposal: SellerProposal;
      approval: { id: string; status: string };
      results: { line_id: string; status: string; reasons?: string[] }[];
    }>(`/api/seller/proposals/${encodeURIComponent(id)}/decide`, {
      method: "POST",
      body: JSON.stringify({ lines, note: note ?? null }),
    });
  }

  // Products
  getProducts(params?: { category?: string; min_price?: number; max_price?: number; search?: string; sort?: string }) {
    const qs = new URLSearchParams();
    if (params?.category) qs.set("category", params.category);
    if (params?.min_price !== undefined) qs.set("min_price", String(params.min_price));
    if (params?.max_price !== undefined) qs.set("max_price", String(params.max_price));
    if (params?.search) qs.set("search", params.search);
    if (params?.sort) qs.set("sort", params.sort);
    const q = qs.toString();
    return this.request<{ products: any[]; total: number; categories: string[] }>(`/api/products${q ? `?${q}` : ""}`);
  }

  getProduct(id: string) {
    return this.request<any>(`/api/products/${id}`);
  }

  // Cart
  getCart() {
    return this.request<{
      id: string;
      items: CartItem[];
      item_count: number;
      subtotal: number;
      discount_amount: number;
      coupon_code: string | null;
      total: number;
      shipping_address: Address | null;
      billing_address: Address | null;
      billing_same_as_shipping: boolean;
    }>("/api/cart");
  }

  addToCart(productId: string, quantity: number = 1) {
    return this.request<{ status: string; product_id: string; quantity: number }>(
      "/api/cart/items",
      {
        method: "POST",
        body: JSON.stringify({ product_id: productId, quantity }),
      }
    );
  }

  updateCartItem(itemId: string, quantity: number) {
    return this.request<{ status: string }>(`/api/cart/items/${itemId}`, {
      method: "PUT",
      body: JSON.stringify({ quantity }),
    });
  }

  removeCartItem(itemId: string) {
    return this.request<{ status: string }>(`/api/cart/items/${itemId}`, {
      method: "DELETE",
    });
  }

  applyCoupon(code: string) {
    return this.request<{
      status: string;
      code: string;
      discount_amount: number;
      description: string;
    }>("/api/cart/coupon", {
      method: "POST",
      body: JSON.stringify({ code }),
    });
  }

  removeCoupon() {
    return this.request<{ status: string }>("/api/cart/coupon", {
      method: "DELETE",
    });
  }

  updateCartAddress(data: {
    shipping_address?: Address;
    billing_address?: Address;
    billing_same_as_shipping?: boolean;
  }) {
    return this.request<{ status: string }>("/api/cart/address", {
      method: "PUT",
      body: JSON.stringify(data),
    });
  }

  checkout(data: {
    shipping_address: Address;
    billing_address?: Address | null;
    billing_same_as_shipping?: boolean;
    payment_method?: string;
  }) {
    return this.request<{
      order_id: string;
      total: number;
      item_count: number;
      status: string;
      tracking_number: string;
      carrier: string;
    }>("/api/checkout", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  cancelOrder(orderId: string, reason: string) {
    return this.request<{
      order_id: string;
      status: string;
      refund_amount: number;
    }>(`/api/orders/${orderId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
  }

  initiateReturn(orderId: string, reason: string, refundMethod: string) {
    return this.request<{
      return_id: string;
      order_id: string;
      status: string;
      return_label_url: string;
      refund_amount: number;
      refund_method: string;
    }>(`/api/orders/${orderId}/return`, {
      method: "POST",
      body: JSON.stringify({ reason, refund_method: refundMethod }),
    });
  }

  // Orders
  getOrders(status?: string) {
    const q = status ? `?status=${status}` : "";
    return this.request<{ orders: any[]; total: number }>(`/api/orders${q}`);
  }

  getOrder(id: string) {
    return this.request<any>(`/api/orders/${id}`);
  }

  // Profile
  getProfile() {
    return this.request<any>("/api/profile");
  }

  getUserMemories(category?: string) {
    const q = category ? `?category=${encodeURIComponent(category)}` : "";
    return this.request<
      { id: string; category: string; content: string; importance: number; created_at: string }[]
    >(`/api/user/memories${q}`);
  }

  deleteUserMemory(id: string) {
    return this.request<{ deleted: boolean }>(`/api/user/memories/${id}`, {
      method: "DELETE",
    });
  }
}

export const api = new ApiClient();

// Named function exports for convenience (delegates to the singleton)
export function getConversations() {
  return api.getConversations();
}

export function getConversation(id: string) {
  return api.getConversation(id);
}

export function deleteConversation(id: string) {
  return api.deleteConversation(id);
}

export function chat(message: string, conversationId?: string) {
  return api.chat(message, conversationId);
}

export function chatStream(
  message: string,
  conversationId: string | undefined,
  onChunk: (text: string) => void,
) {
  return api.chatStream(message, conversationId, onChunk);
}
