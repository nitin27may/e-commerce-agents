import { z } from "zod";

/**
 * The chat's generative-UI cards: one schema and one registry entry per card
 * kind, plus the fence scanner every consumer shares.
 *
 * The model answers with fenced blocks whose language tag names the card
 * (```product, ```order, ...) and whose body is JSON. Each body is stripped of
 * nulls, validated here and checked for emptiness. A body that fails any of
 * those is dropped, never shown: the user sees the surrounding text and no
 * card, and the console says why (see `warnDropped`). Nothing falls back to
 * rendering the raw fence.
 *
 * The schemas are deliberately permissive ("shaped like something the card can
 * render", not server-side validation). They guard against:
 *   - prompt-injected JSON with control characters or HTML
 *   - upstream tools returning the wrong shape
 *   - models hallucinating extra or missing fields
 *
 * `CARD_REGISTRY` is the only list of card kinds. The parser
 * (components/chat/rich-message.tsx), the follow-up chips (lib/suggestions.ts)
 * and the component table (components/chat/card-registry.tsx, typed against
 * `CardKind` so a missing kind does not compile) all read from it. React
 * components stay out of this file so `lib/` keeps no dependency on
 * `components/`.
 */

// Primitive: a non-empty string with no embedded markup that could be
// re-parsed by ReactMarkdown later.
const safeString = z
  .string()
  .max(2000)
  .transform((s) => s.normalize("NFKC"));

const optionalSafeString = safeString.optional();

const positiveNumber = z.number().finite().min(0).max(1_000_000);

export const ProductDataSchema = z
  .object({
    id: optionalSafeString,
    name: optionalSafeString,
    price: positiveNumber.optional(),
    original_price: positiveNumber.optional(),
    image_url: optionalSafeString,
    rating: z.number().min(0).max(5).optional(),
    review_count: z.number().int().min(0).optional(),
    category: optionalSafeString,
    brand: optionalSafeString,
    description: z.string().max(4000).optional(),
    on_sale: z.boolean().optional(),
  })
  .passthrough();

export const OrderItemSchema = z
  .object({
    name: optionalSafeString,
    quantity: z.number().int().positive().optional(),
    price: positiveNumber.optional(),
    unit_price: positiveNumber.optional(),
    subtotal: positiveNumber.optional(),
    brand: optionalSafeString,
    category: optionalSafeString,
    image_url: optionalSafeString,
  })
  .passthrough();

export const TimelineEventSchema = z
  .object({
    label: optionalSafeString,
    // order-card.tsx renders event.status; backend may emit either field
    status: optionalSafeString,
    date: optionalSafeString,
    completed: z.boolean().optional(),
  })
  .passthrough();

const ShippingAddressObj = z
  .object({
    street: optionalSafeString,
    city: optionalSafeString,
    state: optionalSafeString,
    zip: optionalSafeString,
    country: optionalSafeString,
  })
  .passthrough();

export const OrderDataSchema = z
  .object({
    id: optionalSafeString,
    order_id: optionalSafeString,
    status: optionalSafeString,
    total: positiveNumber.optional(),
    date: optionalSafeString,
    item_count: z.number().int().min(0).optional(),
    items: z.array(OrderItemSchema).max(50).optional(),
    tracking: optionalSafeString,
    carrier: optionalSafeString,
    shipping_address: z.union([safeString, ShippingAddressObj]).optional(),
    timeline: z.array(TimelineEventSchema).max(20).optional(),
    // Populated when the agent has already called check_return_eligibility;
    // order-card.tsx falls back to a client-side heuristic when absent.
    return_eligible: z.boolean().optional(),
  })
  .passthrough();

export const CheckoutDataSchema = z
  .object({
    message: optionalSafeString,
    item_count: z.number().int().min(0).optional(),
    total: positiveNumber.optional(),
    subtotal: positiveNumber.optional(),
    discount: positiveNumber.optional(),
    items: z
      .array(
        z
          .object({
            name: optionalSafeString,
            brand: optionalSafeString,
            quantity: z.number().int().positive().optional(),
            price: positiveNumber.optional(),
            unit_price: positiveNumber.optional(),
            subtotal: positiveNumber.optional(),
          })
          .passthrough()
      )
      .max(50)
      .optional(),
    shipping_address: z.union([safeString, ShippingAddressObj]).optional(),
    address_ready: z.boolean().optional(),
  })
  .passthrough();

export const ReturnDataSchema = z
  .object({
    order_id: optionalSafeString,
    return_id: optionalSafeString,
    status: optionalSafeString,
    return_label_url: optionalSafeString,
    refund_amount: positiveNumber.optional(),
    refund_method: optionalSafeString,
    refund_timeline: optionalSafeString,
  })
  .passthrough();

// ── Generative-UI primitives (Phase 8.4 Stage 3) ──────────────────────────
// Generic shapes for the reusable DataTable/TrendChart/DistributionChart/
// StatTile components (web/src/components/ui/). Not yet registered as their
// own fence tags below — Stage 4 wires each specialist's specific fence
// (e.g. a sentiment-distribution or stock-table tag) to one of these, once
// that specialist's real data shape is known. Exported now so the schema
// and the component it validates for land together, mirroring the existing
// pattern of building the render layer and its guard rail as one unit.

const SEMANTIC_TONE = z.enum(["success", "warning", "info", "destructive", "neutral"]);

export const DataTableColumnSchema = z
  .object({
    key: safeString,
    header: safeString,
    align: z.enum(["left", "center", "right"]).optional(),
  })
  .passthrough();

export const DataTableDataSchema = z
  .object({
    title: optionalSafeString,
    columns: z.array(DataTableColumnSchema).max(10),
    rows: z
      .array(z.record(z.string(), z.union([safeString, positiveNumber, z.boolean(), z.null()])))
      .max(50),
  })
  .passthrough();

export const TrendChartDataSchema = z
  .object({
    title: optionalSafeString,
    xKey: safeString,
    series: z.array(z.object({ key: safeString, label: safeString }).passthrough()).max(5),
    data: z.array(z.record(z.string(), z.union([safeString, z.number()]))).max(100),
  })
  .passthrough();

export const DistributionChartDataSchema = z
  .object({
    title: optionalSafeString,
    valueLabel: optionalSafeString,
    data: z.array(z.object({ label: safeString, value: z.number() }).passthrough()).max(50),
  })
  .passthrough();

export const StatTileDataSchema = z
  .object({
    label: safeString,
    value: z.union([safeString, positiveNumber]),
    hint: optionalSafeString,
    tone: SEMANTIC_TONE.optional(),
  })
  .passthrough();

// ── review-sentiment (Phase 8.4 Stage 4a) ──────────────────────────────────
// Every field optional: analyze_sentiment, get_sentiment_trend, and
// detect_fake_reviews each populate a different subset in one `sentiment`
// fence, so the card renders whichever pieces are present rather than
// requiring all three tool calls before showing anything.

const MonthlyRatingPointSchema = z
  .object({
    month: safeString,
    average_rating: z.number().min(0).max(5),
    review_count: z.number().int().min(0).optional(),
  })
  .passthrough();

export const SentimentDataSchema = z
  .object({
    product_id: optionalSafeString,
    product_name: optionalSafeString,
    overall_sentiment: z.enum(["very_positive", "positive", "mixed", "negative", "very_negative"]).optional(),
    average_rating: z.number().min(0).max(5).optional(),
    total_reviews: z.number().int().min(0).optional(),
    rating_distribution: z.record(z.string(), z.number().int().min(0)).optional(),
    pros: z.array(safeString).max(10).optional(),
    cons: z.array(safeString).max(10).optional(),
    trend: z.enum(["improving", "declining", "stable", "insufficient_data"]).optional(),
    monthly_data: z.array(MonthlyRatingPointSchema).max(24).optional(),
    risk_level: z.enum(["high", "medium", "low"]).optional(),
    suspicious_count: z.number().int().min(0).optional(),
  })
  .passthrough();

// ── inventory-fulfillment (Phase 8.4 Stage 4b) ─────────────────────────────
// Every field optional: check_stock, get_warehouse_availability, and
// get_restock_schedule each populate a different subset in one `inventory`
// fence, mirroring the `sentiment` fence's "whichever tools ran" design.

const WarehouseStockSchema = z
  .object({
    warehouse: optionalSafeString,
    region: optionalSafeString,
    quantity: z.number().int().min(0).optional(),
    low_stock: z.boolean().optional(),
  })
  .passthrough();

const RestockEntrySchema = z
  .object({
    warehouse: optionalSafeString,
    region: optionalSafeString,
    expected_quantity: z.number().int().min(0).optional(),
    expected_date: optionalSafeString,
  })
  .passthrough();

// Phase 8.4 Stage 5 — estimate_shipping's fields. The one genuinely
// interactive case in this fence: each option is a real choice, not just
// display, wired to the re-prompt path (see inventory-card.tsx) since
// there's no direct-mutation endpoint for "select a shipping carrier" —
// unlike cart add-item, which already has one.
const ShipsFromSchema = z
  .object({
    warehouse: optionalSafeString,
    region: optionalSafeString,
    quantity_available: z.number().int().min(0).optional(),
  })
  .passthrough();

const ShippingOptionSchema = z
  .object({
    carrier: safeString,
    speed_tier: optionalSafeString,
    price: positiveNumber,
    delivery_window: optionalSafeString,
  })
  .passthrough();

export const InventoryDataSchema = z
  .object({
    product_id: optionalSafeString,
    product_name: optionalSafeString,
    in_stock: z.boolean().optional(),
    total_quantity: z.number().int().min(0).optional(),
    warehouses: z.array(WarehouseStockSchema).max(20).optional(),
    upcoming_restocks: z.array(RestockEntrySchema).max(20).optional(),
    next_restock: optionalSafeString,
    ships_from: ShipsFromSchema.optional(),
    shipping_options: z.array(ShippingOptionSchema).max(10).optional(),
  })
  .passthrough();

// ── pricing-promotions (Phase 8.4 Stage 4c) ────────────────────────────────
// Every field optional: optimize_cart populates the discount-waterfall
// fields (original_total/savings/total_savings/final_total), get_active_deals
// populates coupons/promotions — same "whichever tools ran" design as 4a/4b.

const SavingsLineSchema = z
  .object({
    type: z.enum(["coupon", "bundle_promotion", "buy_x_get_y", "flash_sale", "loyalty_discount"]),
    code: optionalSafeString,
    name: optionalSafeString,
    description: optionalSafeString,
    tier: optionalSafeString,
    product: optionalSafeString,
    amount: positiveNumber,
  })
  .passthrough();

const DealCouponSchema = z
  .object({
    code: safeString,
    description: optionalSafeString,
    discount_type: z.enum(["percentage", "fixed"]).optional(),
    discount_value: positiveNumber.optional(),
    min_spend: positiveNumber.optional(),
    valid_until: optionalSafeString,
  })
  .passthrough();

const DealPromotionSchema = z
  .object({
    name: safeString,
    type: optionalSafeString,
    end_date: optionalSafeString,
  })
  .passthrough();

export const PricingDataSchema = z
  .object({
    original_total: positiveNumber.optional(),
    savings: z.array(SavingsLineSchema).max(10).optional(),
    total_savings: positiveNumber.optional(),
    final_total: positiveNumber.optional(),
    savings_percentage: z.number().min(0).max(100).optional(),
    coupons: z.array(DealCouponSchema).max(20).optional(),
    promotions: z.array(DealPromotionSchema).max(20).optional(),
  })
  .passthrough();

// ── Card registry ───────────────────────────────────────────────────────────

/** A follow-up chip: the label shown, and the prompt it sends. */
export interface Suggestion {
  label: string;
  prompt: string;
}

/** A two-product `products` fence renders as one comparison card. */
export const ComparisonDataSchema = z.tuple([ProductDataSchema, ProductDataSchema]);

/** The schema behind each card kind; CARD_REGISTRY below must provide exactly these. */
interface CardSchemas {
  product: typeof ProductDataSchema;
  comparison: typeof ComparisonDataSchema;
  order: typeof OrderDataSchema;
  checkout: typeof CheckoutDataSchema;
  return: typeof ReturnDataSchema;
  sentiment: typeof SentimentDataSchema;
  inventory: typeof InventoryDataSchema;
  pricing: typeof PricingDataSchema;
}

export type CardKind = keyof CardSchemas;
export type CardData<K extends CardKind> = z.infer<CardSchemas[K]>;
export type ProductData = CardData<"product">;
export type OrderData = CardData<"order">;
export type CheckoutData = CardData<"checkout">;
export type ReturnData = CardData<"return">;
export type SentimentData = CardData<"sentiment">;
export type InventoryData = CardData<"inventory">;
export type PricingData = CardData<"pricing">;

/** One validated card, discriminated by kind. */
export type Card = { [K in CardKind]: { kind: K; data: CardData<K> } }[CardKind];

export interface CardDefinition<K extends CardKind> {
  schema: CardSchemas[K];
  /** True when the model emits this tag itself. `comparison` is built from a `products` fence. */
  fence: boolean;
  /** A payload that would render a header over nothing (e.g. `{}`) is dropped rather than shown. */
  isEmpty: (data: CardData<K>) => boolean;
  /** Same key, same card: the second copy in one message is dropped. Null never dedupes. */
  dedupeKey: (data: CardData<K>) => string | null;
  /** Follow-up chips when this is the last card in a message. Ordered most-likely-first; three show. */
  suggestions: readonly Suggestion[];
  /**
   * Values the card already displays (prices, ratings, codes, names). A bullet
   * list beside the card whose items mostly repeat these is dropped as a restatement.
   */
  facts: (data: CardData<K>) => string[];
}

const has = (v: unknown): boolean =>
  v != null && v !== "" && !(Array.isArray(v) && v.length === 0) &&
  !(typeof v === "object" && !Array.isArray(v) && Object.keys(v as object).length === 0);

/** "$1,068.43" plus the "$139" form a model writes for whole amounts. */
function moneyFacts(n: number | undefined): string[] {
  if (n == null) return [];
  const formatted = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(n);
  return Number.isInteger(n) ? [formatted, `$${n}`] : [formatted, `$${n.toFixed(2)}`];
}

const productFacts = (p: ProductData): string[] => [
  ...moneyFacts(p.price),
  ...moneyFacts(p.original_price),
  ...(p.rating != null ? [p.rating.toFixed(1)] : []),
];

/** Present sections, so a stock card and a shipping card for one product do not dedupe each other. */
const sectionSignature = (data: Record<string, unknown>, keys: string[]): string =>
  keys.filter((k) => has(data[k])).join("+");

export const CARD_REGISTRY: { [K in CardKind]: CardDefinition<K> } = {
  product: {
    schema: ProductDataSchema,
    fence: true,
    isEmpty: (d) => !d.name,
    dedupeKey: (d) => (d.id ? `product:${d.id}` : d.name ? `product:${d.name}` : null),
    suggestions: [
      { label: "Check stock", prompt: "Is this in stock?" },
      { label: "Show reviews", prompt: "What do the reviews say about this?" },
      { label: "Find similar", prompt: "Show me similar products" },
    ],
    facts: productFacts,
  },
  comparison: {
    schema: ComparisonDataSchema,
    fence: false,
    isEmpty: ([a, b]) => !a.name || !b.name,
    dedupeKey: (pair) => `comparison:${pair.map((p) => p.id ?? p.name ?? "").join(",")}`,
    // More than one product changes the useful follow-up from "tell me about it" to "compare".
    suggestions: [
      { label: "Compare these", prompt: "Compare these products for me" },
      { label: "Check stock", prompt: "Which of these are in stock?" },
      { label: "Any deals?", prompt: "Are any of these on offer?" },
    ],
    facts: (pair) => pair.flatMap(productFacts),
  },
  order: {
    schema: OrderDataSchema,
    fence: true,
    isEmpty: (d) => !d.id && !d.order_id && !has(d.items) && d.total == null && !d.status,
    dedupeKey: (d) => {
      const id = d.id ?? d.order_id;
      return id ? `order:${id}` : null;
    },
    suggestions: [
      { label: "Track this order", prompt: "Where is this order right now?" },
      { label: "Start a return", prompt: "I want to return this order" },
      { label: "Order details", prompt: "Show me the full details of this order" },
    ],
    facts: (d) => [
      ...moneyFacts(d.total),
      ...(d.items ?? []).flatMap((i) => [i.name ?? "", ...moneyFacts(i.unit_price)]),
      d.tracking ?? "",
    ],
  },
  checkout: {
    schema: CheckoutDataSchema,
    fence: true,
    isEmpty: (d) => !has(d.items) && d.item_count == null && d.total == null,
    // No id on a checkout: key on the cart shape (the same checkout is often restated in one message).
    dedupeKey: (d) => `checkout:${d.item_count ?? ""}:${d.total ?? ""}`,
    suggestions: [
      { label: "Apply a coupon", prompt: "Apply my best available coupon" },
      { label: "Shipping options", prompt: "What are my shipping options?" },
      { label: "Place the order", prompt: "Go ahead and place the order" },
    ],
    facts: (d) => [
      ...moneyFacts(d.total),
      ...moneyFacts(d.subtotal),
      ...(d.items ?? []).flatMap((i) => [i.name ?? "", ...moneyFacts(i.unit_price ?? i.price)]),
    ],
  },
  return: {
    schema: ReturnDataSchema,
    fence: true,
    isEmpty: (d) =>
      !d.return_id && !d.order_id && !d.status && d.refund_amount == null && !d.return_label_url,
    dedupeKey: (d) => `return:${d.return_id ?? d.order_id ?? ""}`,
    suggestions: [
      { label: "Return status", prompt: "What is the status of my return?" },
      { label: "Refund timing", prompt: "When will I get my refund?" },
      { label: "Replacement options", prompt: "What can I replace this with?" },
    ],
    facts: (d) => [...moneyFacts(d.refund_amount), d.refund_timeline ?? ""],
  },
  sentiment: {
    schema: SentimentDataSchema,
    fence: true,
    // A product name alone is a header over nothing.
    isEmpty: (d) =>
      !d.overall_sentiment &&
      d.average_rating == null &&
      d.total_reviews == null &&
      !has(d.rating_distribution) &&
      !has(d.pros) &&
      !has(d.cons) &&
      !has(d.monthly_data) &&
      !d.risk_level &&
      d.suspicious_count == null,
    dedupeKey: (d) =>
      `sentiment:${d.product_id ?? d.product_name ?? ""}:${sectionSignature(d, ["rating_distribution", "monthly_data", "risk_level"])}`,
    suggestions: [
      { label: "Show negative reviews", prompt: "What do the negative reviews say?" },
      { label: "Summarise the pros", prompt: "What do reviewers like most about this?" },
      { label: "Compare sentiment", prompt: "How does this compare to similar products?" },
    ],
    facts: (d) => [...(d.average_rating != null ? [d.average_rating.toFixed(1)] : []), ...(d.pros ?? []), ...(d.cons ?? [])],
  },
  inventory: {
    schema: InventoryDataSchema,
    fence: true,
    isEmpty: (d) =>
      d.in_stock == null &&
      d.total_quantity == null &&
      !has(d.warehouses) &&
      !has(d.upcoming_restocks) &&
      !d.next_restock &&
      !has(d.shipping_options),
    dedupeKey: (d) =>
      `inventory:${d.product_id ?? d.product_name ?? ""}:${sectionSignature(d, ["warehouses", "upcoming_restocks", "shipping_options"])}`,
    suggestions: [
      { label: "Estimate delivery", prompt: "How fast can this be delivered to me?" },
      { label: "Other warehouses", prompt: "Which other warehouses have this?" },
      { label: "Restock date", prompt: "When will this be back in stock?" },
    ],
    facts: (d) => [
      ...(d.warehouses ?? []).map((w) => w.warehouse ?? ""),
      ...(d.shipping_options ?? []).flatMap((o) => [o.carrier, ...moneyFacts(o.price)]),
    ],
  },
  pricing: {
    schema: PricingDataSchema,
    fence: true,
    // e.g. optimize_cart could not resolve a cart and the model still emitted the fence.
    isEmpty: (d) => !(d.original_total != null && has(d.savings)) && !has(d.coupons) && !has(d.promotions),
    dedupeKey: (d) =>
      `pricing:${d.original_total ?? ""}:${d.final_total ?? ""}:${d.coupons?.length ?? ""}:${d.promotions?.length ?? ""}`,
    suggestions: [
      { label: "Any better deals?", prompt: "Are there any better deals on this?" },
      { label: "Price history", prompt: "What has the price history been?" },
      { label: "Apply a coupon", prompt: "Do I have any coupons I can use?" },
    ],
    facts: (d) => [
      ...moneyFacts(d.final_total),
      ...moneyFacts(d.original_total),
      ...(d.savings ?? []).flatMap((s) => [s.code ?? "", ...moneyFacts(s.amount)]),
      ...(d.coupons ?? []).map((c) => c.code),
      ...(d.promotions ?? []).map((p) => p.name),
    ],
  },
};

/**
 * Recursively drop keys whose value is `null` (and `null` array elements).
 * The LLM/backend emit `null` for empty fields (e.g. "shipping_address":
 * null), but Zod `.optional()` accepts `undefined`, not `null` — so a stray
 * null would fail the whole schema and drop an otherwise-valid card. Treat
 * null as "absent" before validating.
 */
function dropNulls(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.filter((v) => v !== null).map(dropNulls);
  }
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      if (v === null) continue;
      out[k] = dropNulls(v);
    }
    return out;
  }
  return value;
}

/**
 * Validate a parsed JSON value for `kind`. Returns the sanitised data, or
 * `null` when the schema rejects it or the result would be an empty card.
 */
export function validateCard<K extends CardKind>(kind: K, raw: unknown): CardData<K> | null {
  const def = CARD_REGISTRY[kind] as CardDefinition<K>;
  const result = def.schema.safeParse(dropNulls(raw));
  if (!result.success) return null;
  const data = result.data as CardData<K>;
  return def.isEmpty(data) ? null : data;
}

export function cardDedupeKey(card: Card): string | null {
  return (CARD_REGISTRY[card.kind].dedupeKey as (d: Card["data"]) => string | null)(card.data);
}

export function cardFacts(card: Card): string[] {
  return (CARD_REGISTRY[card.kind].facts as (d: Card["data"]) => string[])(card.data).filter(
    (f) => f.trim().length > 1,
  );
}

// ── Fence scanning ──────────────────────────────────────────────────────────

/**
 * Every tag the model may put on a card fence: each fence kind, plus `products`
 * (an array of products). Longest first, so `products` wins over `product` in
 * an alternation.
 */
export const CARD_FENCE_TAGS: readonly string[] = [
  "products",
  ...(Object.keys(CARD_REGISTRY) as CardKind[]).filter((k) => CARD_REGISTRY[k].fence),
].sort((a, b) => b.length - a.length);

const TAG_ALTERNATION = CARD_FENCE_TAGS.join("|");

/**
 * A complete card fence. The newline after the tag is optional: the streaming
 * transport frames a lone "\n" token as an empty SSE data line and drops it,
 * so ```product\n{...} can arrive as ```product{...}. The body must start with
 * `{` or `[`, so a ```product-ideas prose block is not misread as a card.
 */
export const cardFenceRegex = (): RegExp => new RegExp("```(" + TAG_ALTERNATION + ")\\s*([[{][\\s\\S]*?)```", "g");

/** The opening of a card fence, closed or not; used to hide a half-streamed one. */
export const cardFenceOpenRegex = (): RegExp => new RegExp("```(" + TAG_ALTERNATION + ")", "g");

export function isCardFenceTag(tag: string): boolean {
  return CARD_FENCE_TAGS.includes(tag);
}

/**
 * A dropped card is never shown to the user, so say so in the console: always in
 * development, and in a production build when the page URL carries
 * `?debug=cards`.
 */
export function warnDropped(message: string, detail?: unknown): void {
  let debug = process.env.NODE_ENV !== "production";
  if (!debug && typeof window !== "undefined") {
    try {
      debug = new URLSearchParams(window.location.search).get("debug") === "cards";
    } catch {
      // a locked-down window.location: stay quiet
    }
  }
  if (debug) console.warn(`[chat-cards] ${message}`, detail ?? "");
}

/** Cards from one fence body. A product list of exactly two becomes a comparison. */
function cardsFromFence(tag: string, raw: unknown): Card[] {
  if (tag === "products" || tag === "product") {
    const items = Array.isArray(raw) ? raw : [raw];
    const products = items.map((item) => validateCard("product", item));
    if (items.length === 2 && products[0] && products[1]) {
      const pair = validateCard("comparison", [products[0], products[1]]);
      if (pair) return [{ kind: "comparison", data: pair }];
    }
    return products.filter((p): p is ProductData => p !== null).map((data) => ({ kind: "product" as const, data }));
  }
  const kind = tag as CardKind;
  const data = validateCard(kind, raw);
  return data ? [{ kind, data } as Card] : [];
}

export interface CardFence {
  /** Offset of the opening ``` in the scanned text. */
  start: number;
  /** Offset just past the closing ```. */
  end: number;
  /** Validated cards; empty when the body was malformed, invalid or empty. */
  cards: Card[];
}

/** Every complete card fence in `content`, in order, with its validated cards. */
export function scanCardFences(content: string): CardFence[] {
  const fences: CardFence[] = [];
  for (const match of content.matchAll(cardFenceRegex())) {
    const tag = match[1];
    const start = match.index ?? 0;
    let cards: Card[] = [];
    try {
      cards = cardsFromFence(tag, JSON.parse(match[2].trim()));
      if (cards.length === 0) warnDropped(`dropped a \`${tag}\` fence that failed validation or was empty`, match[2]);
    } catch (err) {
      warnDropped(`dropped a \`${tag}\` fence with malformed JSON`, err);
    }
    fences.push({ start, end: start + match[0].length, cards });
  }
  return fences;
}
