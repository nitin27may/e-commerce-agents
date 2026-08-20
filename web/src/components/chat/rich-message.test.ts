import { describe, expect, it } from "vitest";
import { parseContent, type Segment } from "./rich-message";

const PRODUCT = {
  name: "Sony WH-1000XM5",
  id: "74427b99-8717-4481-8c00-d05dd19b120f",
  price: 299.99,
  original_price: 349.99,
  rating: 4.7,
  review_count: 15,
  category: "Electronics",
  brand: "Sony",
  description: "Premium wireless noise-cancelling headphones.",
};

const PRODUCT_2 = {
  name: "AirPods Max",
  id: "7cf9d9fe-e75a-4e49-b1fb-12bc92d4b1e4",
  price: 449.99,
  rating: 4.5,
  category: "Electronics",
  brand: "Apple",
};

const cardTypes = (segs: Segment[]) => segs.filter((s) => s.type !== "text").map((s) => s.type);
const textOf = (segs: Segment[]) => segs.filter((s) => s.type === "text").map((s) => s.text).join(" ");

describe("parseContent — card fence parsing", () => {
  it("parses a clean fenced product block with newline", () => {
    const content = "Here's a pick:\n```product\n" + JSON.stringify(PRODUCT) + "\n```\nWant more?";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["product"]);
    const card = segs.find((s) => s.type === "product");
    expect(card?.data?.name).toBe("Sony WH-1000XM5");
  });

  it("parses a COLLAPSED fence where the transport dropped the newline", () => {
    // ```product{...}``` — no newline after the marker (the real bug)
    const content = "See details below:```product" + JSON.stringify(PRODUCT) + "```Would you like more?";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["product"]);
    // The raw JSON must NOT leak into a text segment
    expect(textOf(segs)).not.toContain('"id"');
    expect(textOf(segs)).not.toContain("product{");
  });

  it("dedupes the same product when it arrives both clean and collapsed", () => {
    const content =
      "Take a look:```product" + JSON.stringify(PRODUCT) + "```" +
      "Hi! Here it is again:\n```product\n" + JSON.stringify(PRODUCT) + "\n```\nThanks";
    const segs = parseContent(content);
    // Two blocks, same id → exactly one card
    expect(cardTypes(segs)).toEqual(["product"]);
  });

  it("renders a two-product array as a single comparison card", () => {
    const content = "Comparison:\n```products\n" + JSON.stringify([PRODUCT, PRODUCT_2]) + "\n```";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["comparison"]);
  });

  it("renders 3+ products as individual product cards", () => {
    const content = "```products\n" + JSON.stringify([PRODUCT, PRODUCT_2, { ...PRODUCT, id: "x" }]) + "\n```";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["product", "product", "product"]);
  });

  it("does not misread a non-card fenced block as a card", () => {
    const content = "```python\nprint('product')\n```";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual([]);
  });

  it("does not match ```product-ideas (body not starting with { or [)", () => {
    const content = "```product-ideas\nsome list\n```";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual([]);
  });

  it("returns plain text untouched when there is no card", () => {
    const content = "Just a normal answer with no structured data.";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual([]);
    expect(textOf(segs)).toContain("normal answer");
  });

  it("parses a collapsed order fence", () => {
    const order = { id: "48bfb7a1-0b02-4c89-94c9-552d629aaa92", status: "shipped", total: 299.99, carrier: "Overnight Shipping", tracking: "TRK277303722" };
    const content = "Your order:```order" + JSON.stringify(order) + "```Anything else?";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["order"]);
  });

  it("renders a checkout card even when fields are null (shipping_address: null)", () => {
    const checkout = {
      message: "Your cart is ready!",
      item_count: 1,
      subtotal: 45.49,
      discount: 0,
      total: 45.49,
      items: [{ name: "Designing Data-Intensive Applications", brand: "O'Reilly", quantity: 1, unit_price: 45.49, subtotal: 45.49 }],
      shipping_address: null, // backend sends null, not undefined — must not drop the card
      address_ready: false,
    };
    const content = "Your cart:\n```checkout\n" + JSON.stringify(checkout) + "\n```\nProceed?";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["checkout"]);
    // raw JSON must not leak as text
    expect(textOf(segs)).not.toContain("address_ready");
  });

  it("dedupes a checkout that the orchestrator restated twice", () => {
    const checkout = { message: "Your cart is ready!", item_count: 1, total: 45.49, items: [{ name: "Book", unit_price: 45.49, quantity: 1 }], shipping_address: null };
    const content =
      "Here:```checkout" + JSON.stringify(checkout) + "```" +
      "Hi! Again:\n```checkout\n" + JSON.stringify(checkout) + "\n```\nProceed?";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["checkout"]);
  });

  it("parses a return fence — the one card type with no prior coverage", () => {
    const ret = {
      order_id: "48bfb7a1-0b02-4c89-94c9-552d629aaa92",
      return_id: "9a1c1e2a-2222-4c89-94c9-552d629aaa92",
      status: "approved",
      return_label_url: "/api/returns/abc123/label",
      refund_amount: 79.99,
      refund_method: "original_payment",
      refund_timeline: "5-7 business days",
    };
    const content = "Your return is set:\n```return\n" + JSON.stringify(ret) + "\n```\nAnything else?";
    const segs = parseContent(content);
    expect(cardTypes(segs)).toEqual(["return"]);
    expect(segs.find((s) => s.type === "return")?.data?.refund_amount).toBe(79.99);
  });
});

describe("parseContent — never renders raw JSON (Phase 8.2)", () => {
  it("drops a fence that fails schema validation instead of showing the raw JSON", () => {
    // refund_amount must be a non-negative number per ReturnDataSchema —
    // a negative value fails validateCard.
    const invalidReturn = { order_id: "o-1", refund_amount: -50 };
    const content = "Before.\n```return\n" + JSON.stringify(invalidReturn) + "\n```\nAfter.";
    const segs = parseContent(content);

    expect(cardTypes(segs)).toEqual([]);
    expect(textOf(segs)).not.toContain("refund_amount");
    expect(textOf(segs)).not.toContain("```");
    // Surrounding conversational text survives — only the bad fence is dropped.
    expect(textOf(segs)).toContain("Before.");
    expect(textOf(segs)).toContain("After.");
  });

  it("drops a recognized-tag fence with malformed JSON instead of showing the raw text", () => {
    const content = "Before.\n```order\n{not valid json at all\n```\nAfter.";
    const segs = parseContent(content);

    expect(cardTypes(segs)).toEqual([]);
    expect(textOf(segs)).not.toContain("not valid json");
    expect(textOf(segs)).toContain("Before.");
    expect(textOf(segs)).toContain("After.");
  });

  it("drops an unrecognized JSON-shaped fence tag instead of falling through to a raw code block", () => {
    // Simulates an LLM hallucinating a tag this parser was never taught,
    // or a future card type introduced backend-side before this file
    // catches up — the general guard, not the 5-tag-specific one above.
    const content = 'Before.\n```cart\n{"items": ["a", "b"], "total": 12.5}\n```\nAfter.';
    const segs = parseContent(content);

    expect(cardTypes(segs)).toEqual([]);
    expect(textOf(segs)).not.toContain("items");
    expect(textOf(segs)).not.toContain("```");
    expect(textOf(segs)).toContain("Before.");
    expect(textOf(segs)).toContain("After.");
  });
});
