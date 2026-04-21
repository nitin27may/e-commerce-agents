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
  .strict();

export const OrderItemSchema = z
  .object({
    name: optionalSafeString,
    quantity: z.number().int().positive().optional(),
    price: positiveNumber.optional(),
    image_url: optionalSafeString,
  })
  .strict();

export const TimelineEventSchema = z
  .object({
    label: optionalSafeString,
    date: optionalSafeString,
    completed: z.boolean().optional(),
  })
  .strict();

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
    shipping_address: optionalSafeString,
    timeline: z.array(TimelineEventSchema).max(20).optional(),
  })
  .strict();

const ShippingAddressObj = z
  .object({
    street: optionalSafeString,
    city: optionalSafeString,
    state: optionalSafeString,
    zip: optionalSafeString,
    country: optionalSafeString,
  })
  .strict();

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
            quantity: z.number().int().positive().optional(),
            price: positiveNumber.optional(),
          })
          .strict()
      )
      .max(50)
      .optional(),
    shipping_address: z.union([safeString, ShippingAddressObj]).optional(),
    address_ready: z.boolean().optional(),
  })
  .strict();

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
  .strict();

export type CardKind = "product" | "order" | "checkout" | "return";

const SCHEMAS = {
  product: ProductDataSchema,
  order: OrderDataSchema,
  checkout: CheckoutDataSchema,
  return: ReturnDataSchema,
} as const;

/**
 * Validate a parsed JSON value against the schema for `kind`. Returns
 * the sanitised data on success, or `null` if validation fails — which
 * the renderer treats as "drop the card, render the raw text instead".
 */
export function validateCard<K extends CardKind>(
  kind: K,
  raw: unknown
): z.infer<(typeof SCHEMAS)[K]> | null {
  const result = SCHEMAS[kind].safeParse(raw);
  return result.success ? (result.data as z.infer<(typeof SCHEMAS)[K]>) : null;
}
