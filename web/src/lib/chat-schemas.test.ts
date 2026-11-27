import { describe, expect, it } from "vitest";
import {
  CARD_FENCE_TAGS,
  CARD_REGISTRY,
  cardDedupeKey,
  cardFacts,
  scanCardFences,
  validateCard,
  type CardKind,
} from "./chat-schemas";

const KINDS = Object.keys(CARD_REGISTRY) as CardKind[];
const FENCE_KINDS = KINDS.filter((k) => CARD_REGISTRY[k].fence);

/** The smallest payload each kind should accept. */
const MINIMAL: Record<CardKind, unknown> = {
  product: { name: "Sony WH-1000XM5" },
  comparison: [{ name: "Sony WH-1000XM5" }, { name: "AirPods Max" }],
  order: { id: "9dee7fee-d86b-5cca-a912-4987f224d562" },
  checkout: { total: 45.49 },
  return: { status: "approved" },
  sentiment: { average_rating: 4.1 },
  inventory: { in_stock: false },
  pricing: { coupons: [{ code: "WELCOME10" }] },
};

describe("CARD_REGISTRY", () => {
  it("lists every fence tag the model emits, plus `products`, longest first", () => {
    expect([...CARD_FENCE_TAGS].sort()).toEqual(
      ["checkout", "inventory", "order", "pricing", "product", "products", "return", "sentiment"],
    );
    // `products` must precede `product` in the regex alternation, or ```products{...} parses as `product`.
    expect(CARD_FENCE_TAGS.indexOf("products")).toBeLessThan(CARD_FENCE_TAGS.indexOf("product"));
    expect(CARD_FENCE_TAGS).not.toContain("comparison");
  });

  it.each(KINDS)("%s offers at least three follow-up chips", (kind) => {
    expect(CARD_REGISTRY[kind].suggestions.length).toBeGreaterThanOrEqual(3);
  });

  it.each(KINDS)("%s accepts its minimal payload", (kind) => {
    expect(validateCard(kind, MINIMAL[kind])).not.toBeNull();
  });
});

describe("validateCard — empty payloads", () => {
  it.each(FENCE_KINDS)("rejects `{}` for %s, which would render a header over nothing", (kind) => {
    expect(validateCard(kind, {})).toBeNull();
  });

  it("rejects payloads that only carry nulls or empty lists", () => {
    expect(validateCard("order", { id: null, items: [], shipping_address: null })).toBeNull();
    expect(validateCard("pricing", { coupons: [], promotions: [] })).toBeNull();
    expect(validateCard("inventory", { product_id: "p1", warehouses: [] })).toBeNull();
  });

  it("rejects a sentiment or inventory payload that only names the product", () => {
    expect(validateCard("sentiment", { product_name: "Dyson V15 Detect" })).toBeNull();
    expect(validateCard("inventory", { product_name: "Dyson V15 Detect" })).toBeNull();
  });

  it("rejects a product without a name and a comparison missing one", () => {
    expect(validateCard("product", { id: "x", price: 10 })).toBeNull();
    expect(validateCard("comparison", [{ name: "A" }, { price: 3 }])).toBeNull();
  });

  it("still strips nulls before validating, so a null field does not drop a real card", () => {
    expect(validateCard("checkout", { total: 45.49, shipping_address: null })).toEqual({ total: 45.49 });
  });
});

describe("dedupe keys", () => {
  it("keys products and orders by id", () => {
    expect(cardDedupeKey({ kind: "product", data: { id: "p1", name: "A" } })).toBe("product:p1");
    expect(cardDedupeKey({ kind: "order", data: { order_id: "o1" } })).toBe("order:o1");
  });

  it("does not collapse a stock card and a shipping card for the same product", () => {
    const stock = cardDedupeKey({ kind: "inventory", data: { product_id: "p1", warehouses: [{ warehouse: "East" }] } });
    const shipping = cardDedupeKey({
      kind: "inventory",
      data: { product_id: "p1", shipping_options: [{ carrier: "Express", price: 9 }] },
    });
    expect(stock).not.toBe(shipping);
  });
});

describe("cardFacts", () => {
  it("lists the prices and names a card shows, in the forms a model writes them", () => {
    const facts = cardFacts({
      kind: "inventory",
      data: { shipping_options: [{ carrier: "Standard Shipping", price: 5.99 }, { carrier: "Freight", price: 30 }] },
    });
    expect(facts).toEqual(expect.arrayContaining(["Standard Shipping", "$5.99", "$30.00", "$30"]));
  });
});

describe("scanCardFences", () => {
  it("finds a fence whose newline after the tag was dropped", () => {
    const [fence] = scanCardFences('Here:```order{"id":"o1"}```Done');
    expect(fence.cards).toEqual([{ kind: "order", data: { id: "o1" } }]);
    expect(fence.start).toBe(5);
  });

  it("turns a two-product array into one comparison, and three into products", () => {
    const two = scanCardFences('```products\n[{"id":"a","name":"A"},{"id":"b","name":"B"}]\n```');
    expect(two[0].cards.map((c) => c.kind)).toEqual(["comparison"]);
    const three = scanCardFences('```products\n[{"name":"A"},{"name":"B"},{"name":"C"}]\n```');
    expect(three[0].cards.map((c) => c.kind)).toEqual(["product", "product", "product"]);
  });

  it("keeps the valid products of a list and drops the empty ones", () => {
    const [fence] = scanCardFences('```products\n[{"name":"A"},{}, {"name":"C"}]\n```');
    expect(fence.cards.map((c) => c.data)).toEqual([{ name: "A" }, { name: "C" }]);
  });

  it("returns a fence with no cards for malformed JSON, so the caller drops it", () => {
    const [fence] = scanCardFences("```order\n{not json\n```");
    expect(fence.cards).toEqual([]);
  });
});
