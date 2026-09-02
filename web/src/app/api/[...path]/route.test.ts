import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";
import { GET, POST } from "./route";

/**
 * The `/api/*` proxy is the frontend's whole transport path to the
 * orchestrator, so the things that would break it silently are the things
 * worth pinning: forwarding the wrong URL, dropping `Authorization`, passing
 * through a `content-encoding` that no longer describes the body, and
 * buffering an SSE stream so the chat answer arrives in one piece at the end.
 */

function makeRequest(init: {
  method?: string;
  pathname?: string;
  search?: string;
  headers?: Record<string, string>;
  body?: string | null;
  aborted?: boolean;
}): NextRequest {
  const controller = new AbortController();
  if (init.aborted) controller.abort();
  return {
    method: init.method ?? "GET",
    headers: new Headers(init.headers ?? {}),
    nextUrl: { pathname: init.pathname ?? "/api/orders", search: init.search ?? "" },
    body: init.body ?? null,
    signal: controller.signal,
  } as unknown as NextRequest;
}

/**
 * Stub `fetch` with the signature the proxy actually calls it with. Without
 * the cast, `vi.fn(async () => ...)` infers a zero-argument function and
 * `mock.calls[i]` collapses to an empty tuple, so every argument assertion
 * below stops type-checking.
 */
function stubFetch(impl: () => Promise<Response>) {
  const spy = vi.fn(impl as unknown as (url: string, init: RequestInit) => Promise<Response>);
  vi.stubGlobal("fetch", spy);
  return spy;
}

let originalOrchestratorUrl: string | undefined;

beforeEach(() => {
  originalOrchestratorUrl = process.env.ORCHESTRATOR_URL;
  process.env.ORCHESTRATOR_URL = "http://orchestrator:8080";
});

afterEach(() => {
  if (originalOrchestratorUrl === undefined) delete process.env.ORCHESTRATOR_URL;
  else process.env.ORCHESTRATOR_URL = originalOrchestratorUrl;
  vi.unstubAllGlobals();
});

describe("api proxy", () => {
  it("forwards method, path and query string to ORCHESTRATOR_URL", async () => {
    const fetchSpy = stubFetch(async () => new Response("[]", { status: 200 }));

    await GET(makeRequest({ pathname: "/api/orders", search: "?limit=50" }));

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toBe("http://orchestrator:8080/api/orders?limit=50");
    expect(fetchSpy.mock.calls[0][1].method).toBe("GET");
  });

  it("tolerates a trailing slash on ORCHESTRATOR_URL", async () => {
    process.env.ORCHESTRATOR_URL = "http://orchestrator:8080/";
    const fetchSpy = stubFetch(async () => new Response("[]", { status: 200 }));

    await GET(makeRequest({ pathname: "/api/orders" }));

    expect(fetchSpy.mock.calls[0][0]).toBe("http://orchestrator:8080/api/orders");
  });

  it("keeps Authorization, drops hop-by-hop headers, pins accept-encoding", async () => {
    const fetchSpy = stubFetch(async () => new Response("{}", { status: 200 }));

    await GET(
      makeRequest({
        headers: {
          authorization: "Bearer token-123",
          "accept-encoding": "gzip, br",
          connection: "keep-alive",
          host: "localhost:3000",
        },
      }),
    );

    const sent = fetchSpy.mock.calls[0][1].headers as Headers;
    expect(sent.get("authorization")).toBe("Bearer token-123");
    // Pinned to identity rather than dropped: undici substitutes its own
    // default when the header is absent, so dropping it would not actually
    // stop the orchestrator compressing a response — including an SSE stream.
    expect(sent.get("accept-encoding")).toBe("identity");
    expect(sent.get("connection")).toBeNull();
    expect(sent.get("host")).toBeNull();
  });

  it("forwards a request body on POST and not on GET", async () => {
    const fetchSpy = stubFetch(async () => new Response("{}", { status: 200 }));

    await POST(makeRequest({ method: "POST", pathname: "/api/chat/stream", body: "{}" }));
    expect(fetchSpy.mock.calls[0][1].body).toBe("{}");

    await GET(makeRequest({ method: "GET" }));
    expect(fetchSpy.mock.calls[1][1].body).toBeUndefined();
  });

  it("marks SSE responses un-buffered so the chat stream stays live", async () => {
    stubFetch(
      async () =>
        new Response("data: hi\n\n", {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
    );

    const res = await POST(makeRequest({ method: "POST", pathname: "/api/chat/stream", body: "{}" }));

    expect(res.headers.get("x-accel-buffering")).toBe("no");
    expect(res.headers.get("cache-control")).toBe("no-cache, no-transform");
  });

  it("strips response headers that no longer describe the body", async () => {
    stubFetch(
      async () =>
        new Response("{}", {
          status: 200,
          headers: {
            "content-type": "application/json",
            "content-encoding": "gzip",
            "x-request-id": "abc",
          },
        }),
    );

    const res = await GET(makeRequest({}));

    expect(res.headers.get("content-encoding")).toBeNull();
    expect(res.headers.get("content-type")).toBe("application/json");
    // Everything else the orchestrator sets must survive the hop.
    expect(res.headers.get("x-request-id")).toBe("abc");
  });

  it("preserves the upstream status rather than flattening it", async () => {
    stubFetch(async () => new Response("{}", { status: 401 }));

    const res = await GET(makeRequest({}));

    // api.ts drives its whole refresh-and-replay path off a 401, so a proxy
    // that turned this into a 502 would log the user out instead.
    expect(res.status).toBe(401);
  });

  it("answers 502 when the orchestrator is unreachable", async () => {
    stubFetch(async () => {
      throw new Error("ECONNREFUSED");
    });
    vi.spyOn(console, "error").mockImplementation(() => {});

    const res = await GET(makeRequest({}));

    expect(res.status).toBe(502);
    await expect(res.json()).resolves.toEqual({ detail: "The orchestrator is unreachable." });
  });

  it("does not report a cancelled stream as a gateway failure", async () => {
    stubFetch(async () => {
      throw new DOMException("The operation was aborted.", "AbortError");
    });

    const res = await GET(makeRequest({ aborted: true }));

    expect(res.status).toBe(499);
  });
});
