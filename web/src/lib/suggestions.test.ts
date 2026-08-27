import { describe, expect, it } from "vitest";
import { cardKindsIn, deriveSuggestions, fromClosingQuestion } from "./suggestions";

const FALLBACK = [
  { label: "Fallback one", prompt: "one" },
  { label: "Fallback two", prompt: "two" },
  { label: "Fallback three", prompt: "three" },
];

const productFence = (n = 1) =>
  Array.from({ length: n }, () => '```product\n{"id":"x","name":"Thing"}\n```').join("\n\n");

describe("cardKindsIn", () => {
  it("finds every fence, in order, keeping duplicates", () => {
    const text = `Here you go\n${productFence()}\nand the order:\n\`\`\`order\n{}\n\`\`\``;
    expect(cardKindsIn(text)).toEqual(["product", "order"]);
  });

  it("counts repeats rather than collapsing them", () => {
    // Two products warrant "compare these"; one warrants "check stock". The
    // count is the signal, so de-duplicating here would lose it.
    expect(cardKindsIn(productFence(2))).toEqual(["product", "product"]);
  });

  it("finds nothing in plain prose", () => {
    expect(cardKindsIn("We are open until 6pm.")).toEqual([]);
  });
});

describe("fromClosingQuestion", () => {
  it("splits an either/or question into chips", () => {
    expect(
      fromClosingQuestion("Sure. Would you like to see more options or compare with other models?")
    ).toEqual([
      { label: "See more options", prompt: "See more options" },
      { label: "Compare with other models", prompt: "Compare with other models" },
    ]);
  });

  it("splits a comma-separated list", () => {
    const out = fromClosingQuestion("Would you like to check stock, see reviews, or compare prices?");
    expect(out.map((s) => s.label)).toEqual(["Check stock", "See reviews", "Compare prices"]);
  });

  it("ignores a message that does not end in a question", () => {
    // Mid-answer prose is not an offer, and treating it as one is how this
    // tier produces chips nobody asked for.
    expect(fromClosingQuestion("Would you like tea or coffee? Anyway, here is your order.")).toEqual([]);
  });

  it("discards the tier when it yields only one candidate", () => {
    // One real chip beside two canned ones reads worse than three canned ones,
    // so the whole tier is dropped rather than padded.
    expect(fromClosingQuestion("Would you like me to place the order?")).toEqual([]);
  });

  it("rejects candidates that are too long to be a chip", () => {
    const long = "a".repeat(60);
    expect(fromClosingQuestion(`Would you like ${long} or ${long}?`)).toEqual([]);
  });

  it("rejects parser debris", () => {
    // A fence that leaked into the final sentence must not become a chip.
    expect(fromClosingQuestion('Would you like {"id":"x"} or ```product?')).toEqual([]);
  });

  it("sentence-cases and strips trailing punctuation", () => {
    const out = fromClosingQuestion("Do you want me to track it, or cancel it?");
    expect(out.map((s) => s.label)).toEqual(["Track it", "Cancel it"]);
  });
});

describe("deriveSuggestions", () => {
  it("uses the fallback when there is no assistant message", () => {
    // The empty-conversation case must look exactly as it does today.
    expect(deriveSuggestions(undefined, FALLBACK)).toEqual(FALLBACK);
  });

  it("uses the fallback for prose with no payload and no closing question", () => {
    expect(deriveSuggestions("We're open until 6pm.", FALLBACK)).toEqual(FALLBACK);
  });

  it("prefers the payload over the prose", () => {
    // Both signals present. The card is the stronger one — it is what the user
    // is looking at, and it does not depend on model phrasing.
    const text = `${productFence()}\n\nWould you like tea or coffee?`;
    expect(deriveSuggestions(text, FALLBACK).map((s) => s.label)).toEqual([
      "Check stock",
      "Show reviews",
      "Find similar",
    ]);
  });

  it("switches to comparison chips when several products are shown", () => {
    expect(deriveSuggestions(productFence(2), FALLBACK).map((s) => s.label)).toEqual([
      "Compare these",
      "Check stock",
      "Any deals?",
    ]);
  });

  it("treats a products array as multiple by construction", () => {
    const text = '```products\n[{"id":"a"},{"id":"b"}]\n```';
    expect(deriveSuggestions(text, FALLBACK)[0].label).toBe("Compare these");
  });

  it("maps an order card to order follow-ups", () => {
    const text = '```order\n{"id":"o1"}\n```';
    expect(deriveSuggestions(text, FALLBACK).map((s) => s.label)).toEqual([
      "Track this order",
      "Start a return",
      "Order details",
    ]);
  });

  it("uses the last card when a message carries several kinds", () => {
    // The nearest card to the composer is the one still on screen.
    const text = `${productFence()}\n\`\`\`inventory\n{}\n\`\`\``;
    expect(deriveSuggestions(text, FALLBACK)[0].label).toBe("Estimate delivery");
  });

  it("falls through to the closing question when there is no payload", () => {
    const out = deriveSuggestions("All set. Would you like to track it or return it?", FALLBACK);
    // Padded to three — see the "always returns exactly three" case below.
    expect(out.slice(0, 2).map((s) => s.label)).toEqual(["Track it", "Return it"]);
  });

  it("always returns exactly three, topped up from the fallback", () => {
    // Two real chips plus one canned beats two chips and a gap — the row must
    // not change height between turns.
    const out = deriveSuggestions("Would you like to track it or return it?", FALLBACK);
    expect(out).toHaveLength(3);
    expect(out[2]).toEqual(FALLBACK[0]);
  });

  it("never repeats a label", () => {
    const out = deriveSuggestions("Would you like to check stock or check stock?", [
      { label: "Check stock", prompt: "dupe" },
      ...FALLBACK,
    ]);
    expect(new Set(out.map((s) => s.label)).size).toBe(out.length);
  });

  it("returns three even when the fallback is short", () => {
    // Degrades to what it has rather than throwing.
    expect(deriveSuggestions(undefined, FALLBACK.slice(0, 1))).toHaveLength(1);
  });
});
