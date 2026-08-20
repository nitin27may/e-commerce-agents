import { z } from "zod";

/**
 * Schemas the chat renderer uses to validate JSON it pulls out of LLM
 * code-fenced blocks. Anything that doesn't pass `safeParse` falls
 * back to the raw markdown path — never to a half-rendered card.
 *
 * Keep these *deliberately permissive*: the goal is "the data is
 * shaped like an object the card knows how to render", not strict
 * server-side validation. The agent backend already normalises shapes;
 * Zod here is a defence-in-depth gate against:
 *   - prompt-injected JSON with control characters or HTML
 *   - upstream tools returning the wrong shape
 *   - LLMs hallucinating extra/missing fields
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

export const InventoryDataSchema = z
  .object({
    product_id: optionalSafeString,
    product_name: optionalSafeString,
    in_stock: z.boolean().optional(),
    total_quantity: z.number().int().min(0).optional(),
    warehouses: z.array(WarehouseStockSchema).max(20).optional(),
    upcoming_restocks: z.array(RestockEntrySchema).max(20).optional(),
    next_restock: optionalSafeString,
  })
  .passthrough();

export type CardKind = "product" | "order" | "checkout" | "return" | "sentiment" | "inventory";

const SCHEMAS = {
  product: ProductDataSchema,
  order: OrderDataSchema,
  checkout: CheckoutDataSchema,
  return: ReturnDataSchema,
  sentiment: SentimentDataSchema,
  inventory: InventoryDataSchema,
} as const;

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
 * Validate a parsed JSON value against the schema for `kind`. Returns
 * the sanitised data on success, or `null` if validation fails — which
 * the renderer treats as "drop the card, render the raw text instead".
 */
export function validateCard<K extends CardKind>(
  kind: K,
  raw: unknown
): z.infer<(typeof SCHEMAS)[K]> | null {
  const result = SCHEMAS[kind].safeParse(dropNulls(raw));
  return result.success ? (result.data as z.infer<(typeof SCHEMAS)[K]>) : null;
}
