import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

/**
 * `chatStream()`'s SSE parser must route structured, non-text frames (the
 * `node`/`handoff`/`checkpoint`/`request_info`/`error` events a non-"tool"
 * orchestration mode emits — see `orchestrator/routes/chat.py`) to
 * `onOrchestrationEvent`, not `onChunk` — falling through to `onChunk`
 * would render their raw JSON payload as if it were part of the assistant's
 * visible reply. `step`/`metadata`/plain-text behavior must stay exactly as
 * before. `delta` frames (a specialist's own live-streamed preview) route
 * to their own `onDeltaChunk` callback, not `onChunk` (Phase 8.1) — see the
 * second test below.
 */

function fakeStreamResponse(rawBody: string) {
  const encoder = new TextEncoder();
  let sent = false;
  return {
    status: 200,
    ok: true,
    body: {
      getReader() {
        return {
          async read() {
            if (sent) return { done: true, value: undefined };
            sent = true;
            return { done: false, value: encoder.encode(rawBody) };
          },
          releaseLock() {},
        };
      },
    },
    json: async () => ({}),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("chatStream SSE parsing", () => {
  it("routes structured frames to onOrchestrationEvent, not onChunk, and keeps step/metadata/text intact", async () => {
    const raw = [
      "data: Hello ",
      "",
      'event: node\ndata: {"node_id":"math","phase":"enter"}',
      "",
      'event: step\ndata: {"tool_name":"lookup","status":"success"}',
      "",
      "data:  world",
      "",
      'event: metadata\ndata: {"conversation_id":"c1","agents_involved":["orchestrator","math"]}',
      "",
      "data: [DONE]",
      "",
    ].join("\n");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(fakeStreamResponse(raw))
    );

    const chunks: string[] = [];
    const orchestrationEvents: Array<{ name: string; data: unknown }> = [];
    const steps: unknown[] = [];

    const metadata = await api.chatStream(
      "hi",
      undefined,
      (chunk) => chunks.push(chunk),
      undefined,
      {
        onStep: (step) => steps.push(step),
        onOrchestrationEvent: (name, data) => orchestrationEvents.push({ name, data }),
      }
    );

    expect(chunks).toEqual(["Hello ", " world"]);
    expect(chunks.join("")).not.toContain("node_id");

    expect(orchestrationEvents).toEqual([{ name: "node", data: { node_id: "math", phase: "enter" } }]);

    expect(steps).toEqual([{ tool_name: "lookup", status: "success" }]);

    expect(metadata).toEqual({ conversation_id: "c1", agents_involved: ["orchestrator", "math"] });
  });

  it("routes a `delta` frame to onDeltaChunk, not onChunk (Phase 8.1 — no longer double-persisted)", async () => {
    const raw = ["event: delta\ndata: specialist chunk", "", "data: [DONE]", ""].join("\n");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(fakeStreamResponse(raw))
    );

    const chunks: string[] = [];
    const deltaChunks: string[] = [];
    await api.chatStream("hi", undefined, (chunk) => chunks.push(chunk), undefined, {
      onDeltaChunk: (chunk) => deltaChunks.push(chunk),
    });

    // A `delta` frame is a specialist's live preview, not the final answer —
    // it must never reach the same buffer onChunk writes to (that's what
    // caused every tool-mode answer that called a specialist to render
    // twice, in independently-worded restatements, with a duplicated card
    // fence). See orchestrator/routes/chat.py's streaming consumer loop.
    expect(chunks).toEqual([]);
    expect(deltaChunks).toEqual(["specialist chunk"]);
  });

  it("keeps a lone-newline chunk and a blank-line chunk exactly as framed (#80)", async () => {
    // The server frames "\n" as two empty data lines and "a\n\nb" as three
    // data lines; the reader must rejoin them with "\n", not drop them.
    const raw = [
      "data: item one",
      "",
      "data: ",
      "data: ",
      "",
      "data: a",
      "data: ",
      "data: b",
      "",
      "data: [DONE]",
      "",
      "",
    ].join("\n");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fakeStreamResponse(raw)));

    const chunks: string[] = [];
    await api.chatStream("hi", undefined, (chunk) => chunks.push(chunk));

    expect(chunks).toEqual(["item one", "\n", "a\n\nb"]);
  });

  it("routes a replay_miss frame to onReplayMiss only: not chat text, not the generic hook", async () => {
    const raw = ['event: replay_miss\ndata: {"message":"Not recorded."}', "", "data: [DONE]", ""].join("\n");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fakeStreamResponse(raw)));

    const chunks: string[] = [];
    const generic: string[] = [];
    const misses: Array<{ message: string }> = [];

    await api.chatStream("hi", undefined, (c) => chunks.push(c), undefined, {
      onOrchestrationEvent: (name) => generic.push(name),
      onReplayMiss: (m) => misses.push(m),
    });

    expect(misses).toEqual([{ message: "Not recorded." }]);
    expect(chunks).toEqual([]);
    expect(generic).toEqual([]);
  });
});
