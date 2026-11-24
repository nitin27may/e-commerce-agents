import { beforeEach, describe, expect, it } from "vitest";
import { buildChatFrames, chunkText, data, encodeFrame, findAnswer, previewFetch, resetPreviewState } from "./mock-api";

const chip = (id: string) => data.tour.chips.find((c) => (c as { id: string }).id === id) as { prompt: string };

async function readStream(res: Response): Promise<string> {
  return await res.text();
}

describe("preview mock api", () => {
  beforeEach(() => resetPreviewState());

  it("has a recording for every tour chip", () => {
    for (const c of data.tour.chips as { id: string; prompt: string }[]) {
      expect(findAnswer(c.prompt), c.id).toBeDefined();
    }
  });

  it("matches a prompt ignoring case and whitespace", () => {
    expect(findAnswer("  IS the  Sony WH-1000XM5 in stock? ")?.id).toBe("stock");
  });

  it("chunks text so it rejoins exactly", () => {
    const text = "one two  three\nfour ```json\n{}\n``` five";
    expect(chunkText(text).join("")).toBe(text);
  });

  it("builds a tool-mode turn: steps, delta preview, text, metadata, done, in that order", () => {
    const frames = buildChatFrames(chip("stock").prompt, "c1");
    const events = frames.map((f) => f.event);
    expect(events[0]).toBe("step");
    expect(events.indexOf("delta")).toBeGreaterThan(events.lastIndexOf("step"));
    expect(events.indexOf("")).toBeGreaterThan(events.lastIndexOf("delta"));
    expect(events.at(-2)).toBe("metadata");
    expect(frames.at(-1)).toMatchObject({ event: "", data: "[DONE]" });
    const text = frames.filter((f) => f.event === "" && f.data !== "[DONE]").map((f) => f.data).join("");
    expect(text).toBe(findAnswer(chip("stock").prompt)?.text);
    expect(JSON.parse(frames.find((f) => f.event === "metadata")!.data)).toMatchObject({ conversation_id: "c1" });
  });

  it("emits node frames for a workflow mode and a run frame for the approval pause", () => {
    const frames = buildChatFrames(chip("return-approval").prompt, "c2");
    const nodes = frames.filter((f) => f.event === "node").map((f) => JSON.parse(f.data));
    expect(nodes[0]).toMatchObject({ node_id: "check-eligibility", phase: "enter" });
    const run = frames.find((f) => f.event === "run");
    expect(JSON.parse(run!.data)).toMatchObject({ pending_approval: true });
  });

  it("answers an unrecorded prompt with the replay_miss notice and nothing else", () => {
    const frames = buildChatFrames("What is the airspeed velocity of an unladen swallow?", "c3");
    expect(frames.map((f) => f.event)).toEqual(["replay_miss", "metadata", ""]);
    expect(JSON.parse(frames[0].data).message).toBe(
      "This preview replays recorded answers; run it locally for the real thing.",
    );
  });

  it("encodes multi-line data as one data: line per line", () => {
    expect(encodeFrame({ event: "delta", data: "a\nb", delayMs: 0 })).toBe("event: delta\ndata: a\ndata: b\n\n");
    expect(encodeFrame({ event: "", data: "[DONE]", delayMs: 0 })).toBe("data: [DONE]\n\n");
  });

  it("serves the miss over the stream endpoint without recording a conversation", async () => {
    const res = await previewFetch("/api/chat/stream", {
      method: "POST",
      body: JSON.stringify({ message: "tell me a joke" }),
    });
    const body = await readStream(res);
    expect(body).toContain("event: replay_miss");
    const list = await (await previewFetch("/api/conversations")).json();
    expect(list).toEqual([]);
  });

  it("logs in the tour user only, and refuses writes", async () => {
    const ok = await previewFetch("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: "tour@example.com", password: "tour123" }),
    });
    expect(ok.status).toBe(200);
    expect((await ok.json()).user.email).toBe("tour@example.com");
    const bad = await previewFetch("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: "x@example.com", password: "nope" }),
    });
    expect(bad.status).toBe(400);
    expect((await previewFetch("/api/cart/items", { method: "POST", body: "{}" })).status).toBe(403);
  });

  it("filters the catalog and serves a product detail", async () => {
    const list = await (await previewFetch("/api/products?search=sony&sort=price_asc")).json();
    expect(list.products.length).toBeGreaterThan(0);
    expect(list.products.every((p: { name: string; brand: string }) => /sony/i.test(p.name + p.brand))).toBe(true);
    const detail = await previewFetch(`/api/products/${list.products[0].id}`);
    expect(detail.status).toBe(200);
    expect((await previewFetch("/api/products/nope")).status).toBe(404);
  });

  it("carries no prompt or request text in the data beyond the tour prompts", () => {
    const text = JSON.stringify(data);
    expect(text).not.toContain("You are an");
    expect(text).not.toContain("instructions");
  });
});
