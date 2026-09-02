/**
 * Same-origin proxy for every `/api/*` call the browser makes.
 *
 * Why this exists: `NEXT_PUBLIC_*` is inlined into the client bundle at build
 * time, and the orchestrator's address does not exist until the infrastructure
 * is provisioned. Baking it in means the image has to be rebuilt once the FQDN
 * is known, which is what makes a one-command deploy impossible.
 *
 * With this handler the browser only ever talks to its own origin. The
 * orchestrator's address becomes `ORCHESTRATOR_URL` — a *server-side* variable
 * read per request — so one image runs against any backend, the orchestrator
 * needs no public ingress at all, and there is no CORS to configure.
 *
 * A `rewrites()` entry in `next.config.ts` would not work: Next evaluates
 * `rewrites()` during `next build` and bakes the result into
 * `routes-manifest.json`, so the destination would be a build-time constant —
 * the same problem in a new place.
 */
import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

/** Hop-by-hop headers (RFC 9110 6.1) plus the ones undici must recompute. */
const STRIP_REQUEST_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
  // See the `accept-encoding: identity` line in proxy() below.
  "accept-encoding",
]);

const STRIP_RESPONSE_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "content-encoding",
  "content-length",
]);

function orchestratorUrl(): string {
  return (process.env.ORCHESTRATOR_URL ?? "http://localhost:8080").replace(/\/+$/, "");
}

async function proxy(req: NextRequest): Promise<Response> {
  const target = `${orchestratorUrl()}${req.nextUrl.pathname}${req.nextUrl.search}`;

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!STRIP_REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });

  // Set rather than merely dropped: letting the orchestrator gzip a response
  // that undici then hands back already decoded would leave a passed-through
  // `content-encoding` describing a body that is no longer encoded, and gzip
  // on `text/event-stream` is a way to buffer a live stream. Deleting the
  // header is not enough — undici substitutes its own default when it is
  // absent, so the value has to be pinned.
  headers.set("accept-encoding", "identity");

  const hasBody = req.method !== "GET" && req.method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? req.body : undefined,
      // undici requires `duplex` whenever the body is a stream.
      ...(hasBody ? { duplex: "half" } : {}),
      redirect: "manual",
      cache: "no-store",
      // Cancelling a streaming chat turn in the browser must cancel the
      // upstream request too, or the orchestrator keeps generating into a
      // socket nobody is reading.
      signal: req.signal,
    } as RequestInit);
  } catch (err) {
    if (req.signal.aborted) {
      // The user cancelled. Not a gateway failure.
      return new Response(null, { status: 499 });
    }
    console.error(`[api-proxy] ${req.method} ${req.nextUrl.pathname} -> ${target} failed`, err);
    return Response.json({ detail: "The orchestrator is unreachable." }, { status: 502 });
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!STRIP_RESPONSE_HEADERS.has(key.toLowerCase())) responseHeaders.set(key, value);
  });

  // Most reverse proxies buffer a proxied response by default, which turns a
  // token-by-token SSE stream into one delivery at the end — the stream still
  // "works" and the UI stops being live.
  if (responseHeaders.get("content-type")?.includes("text/event-stream")) {
    responseHeaders.set("cache-control", "no-cache, no-transform");
    responseHeaders.set("x-accel-buffering", "no");
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;
