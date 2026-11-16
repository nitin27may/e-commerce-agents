import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, RefreshUnavailableError } from "./api";

/**
 * Refresh tokens rotate: each is single-use and presenting a spent one revokes
 * the session. The client therefore has to (1) store the successor it is handed
 * and (2) never have two refreshes in flight with the same token — including
 * across tabs, where the localStorage copy is the source of truth.
 */

type Call = { url: string; body: unknown; auth: string | null };

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** A fake backend: `/api/auth/refresh` rotates; every other route needs the current access token. */
function backend(opts: { refreshDelayMs?: number; failRefresh?: boolean } = {}) {
  const calls: Call[] = [];
  let access = "access-0";
  let refresh = "refresh-0";
  let n = 0;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    const body = init?.body ? JSON.parse(init.body as string) : null;
    calls.push({ url, body, auth: headers.get("Authorization") });
    if (url.endsWith("/api/auth/refresh")) {
      if (opts.refreshDelayMs) await new Promise((r) => setTimeout(r, opts.refreshDelayMs));
      // Spent or unknown tokens are rejected (the server would also revoke the family).
      if (opts.failRefresh || body.refresh_token !== refresh) return json(401, { detail: "Invalid refresh token" });
      n += 1;
      access = `access-${n}`;
      refresh = `refresh-${n}`;
      return json(200, { access_token: access, refresh_token: refresh });
    }
    return headers.get("Authorization") === `Bearer ${access}` ? json(200, { ok: true }) : json(401, {});
  });
  return {
    calls,
    fetchMock,
    refreshCalls: () => calls.filter((c) => c.url.endsWith("/api/auth/refresh")),
    expireAccess: () => {
      access = `${access}-rotated-away`;
    },
  };
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("ecommerce_access_token", "access-0");
  localStorage.setItem("ecommerce_refresh_token", "refresh-0");
  api.setToken("access-0");
  api.setRefreshToken("refresh-0");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("refresh-token rotation in the client", () => {
  it("stores the rotated refresh token and replays the failed request with the new access token", async () => {
    const b = backend();
    vi.stubGlobal("fetch", b.fetchMock);
    b.expireAccess();

    await expect(api.getConversations()).resolves.toEqual({ ok: true });

    expect(localStorage.getItem("ecommerce_refresh_token")).toBe("refresh-1");
    expect(localStorage.getItem("ecommerce_access_token")).toBe("access-1");
    expect(b.refreshCalls()).toHaveLength(1);
  });

  it("sends the successor, not the original, on the next refresh", async () => {
    const b = backend();
    vi.stubGlobal("fetch", b.fetchMock);

    b.expireAccess();
    await api.getConversations();
    b.expireAccess();
    await api.getConversations();

    expect(b.refreshCalls().map((c) => (c.body as { refresh_token: string }).refresh_token)).toEqual([
      "refresh-0",
      "refresh-1",
    ]);
    expect(localStorage.getItem("ecommerce_refresh_token")).toBe("refresh-2");
  });

  it("single-flights simultaneous 401s: one refresh, every request replayed, nobody logged out", async () => {
    const b = backend({ refreshDelayMs: 20 });
    vi.stubGlobal("fetch", b.fetchMock);
    b.expireAccess();

    const results = await Promise.all([api.getConversations(), api.getConversations(), api.getConversations()]);

    expect(results).toEqual([{ ok: true }, { ok: true }, { ok: true }]);
    expect(b.refreshCalls()).toHaveLength(1);
    expect(localStorage.getItem("ecommerce_refresh_token")).toBe("refresh-1");
  });

  it("keeps its refresh token when the server does not rotate (flag off / oauth mode)", async () => {
    let access = "access-0";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/api/auth/refresh")) {
          access = "access-1";
          return json(200, { access_token: access });
        }
        return new Headers(init?.headers).get("Authorization") === `Bearer ${access}` ? json(200, {}) : json(401, {});
      }),
    );
    access = "something-else";

    await api.getConversations();

    expect(localStorage.getItem("ecommerce_refresh_token")).toBe("refresh-0");
    expect(localStorage.getItem("ecommerce_access_token")).toBe("access-1");
  });

  it("adopts another tab's rotation instead of logging out when its own refresh is rejected", async () => {
    const b = backend();
    // Tab B rotates first; tab A (this one) still holds refresh-0 in memory but
    // localStorage already has the successor, which refreshOnce() re-reads.
    localStorage.setItem("ecommerce_refresh_token", "refresh-1");
    localStorage.setItem("ecommerce_access_token", "access-1");
    // Server state matches tab B's rotation.
    await b.fetchMock("/api/auth/refresh", { method: "POST", body: JSON.stringify({ refresh_token: "refresh-0" }) });
    vi.stubGlobal("fetch", b.fetchMock);
    api.setToken("access-0"); // this tab's stale in-memory access token

    await expect(api.getConversations()).resolves.toEqual({ ok: true });

    // It used the stored successor, not its stale in-memory one.
    const sent = b.refreshCalls().map((c) => (c.body as { refresh_token: string }).refresh_token);
    expect(sent.at(-1)).toBe("refresh-1");
    expect(localStorage.getItem("ecommerce_refresh_token")).toBe("refresh-2");
  });

  it("does not log out when another tab won the race: its rejected refresh adopts the stored successor", async () => {
    let access = "access-0";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/api/auth/refresh")) {
          // Meanwhile another tab (no Web Lock in jsdom) rotated and stored its result.
          localStorage.setItem("ecommerce_refresh_token", "refresh-other");
          localStorage.setItem("ecommerce_access_token", "access-other");
          access = "access-other";
          return json(401, { detail: "Invalid refresh token" });
        }
        return new Headers(init?.headers).get("Authorization") === `Bearer ${access}` ? json(200, { ok: 1 }) : json(401, {});
      }),
    );
    access = "stale";

    await expect(api.getConversations()).resolves.toEqual({ ok: 1 });
    expect(localStorage.getItem("ecommerce_refresh_token")).toBe("refresh-other");
  });

  it("clears the session when the refresh token is genuinely dead", async () => {
    const b = backend({ failRefresh: true });
    vi.stubGlobal("fetch", b.fetchMock);
    b.expireAccess();

    await expect(api.getConversations()).rejects.toThrow(/Session expired/);

    expect(localStorage.getItem("ecommerce_refresh_token")).toBeNull();
    expect(localStorage.getItem("ecommerce_access_token")).toBeNull();
  });

  it("clears the session on a 403 from refresh (deactivated account) too", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.endsWith("/api/auth/refresh") ? json(403, { detail: "Account is deactivated" }) : json(401, {}),
      ),
    );

    await expect(api.getConversations()).rejects.toThrow(/Session expired/);
    expect(localStorage.getItem("ecommerce_refresh_token")).toBeNull();
  });

  describe("a refresh that fails for a reason that says nothing about the session", () => {
    function failingRefresh(respond: () => Response | Promise<Response>) {
      const fetchMock = vi.fn(async (url: string) => (url.endsWith("/api/auth/refresh") ? respond() : json(401, {})));
      vi.stubGlobal("fetch", fetchMock);
      return fetchMock;
    }

    it("keeps the session on a 429 and surfaces a retryable error that honours Retry-After", async () => {
      failingRefresh(
        () => new Response("{}", { status: 429, headers: { "Content-Type": "application/json", "Retry-After": "42" } }),
      );

      const err = await api.getConversations().catch((e: unknown) => e);

      expect(err).toBeInstanceOf(RefreshUnavailableError);
      expect((err as RefreshUnavailableError).retryAfterSeconds).toBe(42);
      expect((err as Error).message).toMatch(/42 seconds/);
      expect(localStorage.getItem("ecommerce_refresh_token")).toBe("refresh-0");
      expect(localStorage.getItem("ecommerce_access_token")).toBe("access-0");
    });

    it("keeps the session on a 5xx", async () => {
      failingRefresh(() => json(503, { detail: "down" }));

      await expect(api.getConversations()).rejects.toBeInstanceOf(RefreshUnavailableError);
      expect(localStorage.getItem("ecommerce_refresh_token")).toBe("refresh-0");
    });

    it("keeps the session when the network drops", async () => {
      failingRefresh(() => {
        throw new TypeError("Failed to fetch");
      });

      await expect(api.getConversations()).rejects.toBeInstanceOf(RefreshUnavailableError);
      expect(localStorage.getItem("ecommerce_refresh_token")).toBe("refresh-0");
    });

    it("still single-flights: simultaneous 401s share one refresh even when it is refused", async () => {
      const fetchMock = failingRefresh(() => json(429, {}));

      const results = await Promise.allSettled([api.getConversations(), api.getConversations()]);

      expect(results.every((r) => r.status === "rejected")).toBe(true);
      expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/auth/refresh"))).toHaveLength(1);
      expect(localStorage.getItem("ecommerce_refresh_token")).toBe("refresh-0");
    });

    it("works again once the limiter lets the refresh through", async () => {
      let limited = true;
      let access = "access-0";
      vi.stubGlobal(
        "fetch",
        vi.fn(async (url: string, init?: RequestInit) => {
          if (url.endsWith("/api/auth/refresh")) {
            if (limited) return json(429, {});
            access = "access-1";
            return json(200, { access_token: access, refresh_token: "refresh-1" });
          }
          return new Headers(init?.headers).get("Authorization") === `Bearer ${access}` ? json(200, { ok: 1 }) : json(401, {});
        }),
      );
      access = "rotated-away";

      await expect(api.getConversations()).rejects.toBeInstanceOf(RefreshUnavailableError);
      limited = false;
      await expect(api.getConversations()).resolves.toEqual({ ok: 1 });
      expect(localStorage.getItem("ecommerce_refresh_token")).toBe("refresh-1");
    });
  });
});
