import { formatDate } from "@/lib/format";

/**
 * Display helpers for values the cards receive in their backend form: enum
 * slugs, region codes, ISO dates and model-written delivery windows. Money and
 * full dates go through lib/format.ts; these cover what that does not.
 */

const ENUM_LABELS: Record<string, string> = {
  buy_x_get_y: "Buy X, get Y",
  bundle_promotion: "Bundle deal",
  original_payment: "Original payment method",
  store_credit: "Store credit",
};

/**
 * "flash_sale" -> "Flash sale". Sentence case, with a few hand-written exceptions.
 * Text that is already prose ("Shipped via UPS") passes through untouched.
 */
export function humanizeEnum(value: string | null | undefined): string {
  if (!value) return "";
  const known = ENUM_LABELS[value.toLowerCase()];
  if (known) return known;
  if (!/^[a-z0-9_-]+$/.test(value)) return value;
  const words = value.replace(/[_-]+/g, " ").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Order ids are UUIDs; the first eight characters are what every screen shows. */
export function shortOrderId(id: string | null | undefined): string {
  if (!id) return "";
  return id.slice(0, 8);
}

/**
 * A carrier name with its speed tier, without saying the tier twice:
 * ("Standard Shipping", "standard") -> "Standard Shipping".
 */
export function shippingLabel(carrier: string, speedTier?: string): string {
  if (!speedTier || carrier.toLowerCase().includes(speedTier.toLowerCase())) return carrier;
  return `${carrier} (${humanizeEnum(speedTier)})`;
}

/** "1-1 business days" -> "1 business day"; other windows pass through. */
export function formatDeliveryWindow(window: string | null | undefined): string {
  if (!window) return "";
  const range = window.match(/^(\d+)\s*[-–]\s*(\d+)\s+(.+)$/);
  if (range && range[1] === range[2]) {
    const n = Number(range[1]);
    const unit = n === 1 ? range[3].replace(/s$/, "") : range[3];
    return `${n} ${unit}`;
  }
  return window;
}

const MONTH = /^(\d{4})-(\d{2})$/;

/**
 * Axis labels for "YYYY-MM" points: "May", or "May 2026" style ("May '26") when
 * the series spans more than one year. Anything else passes through.
 */
export function monthLabels(months: string[]): string[] {
  const years = new Set(months.map((m) => MONTH.exec(m)?.[1]).filter(Boolean));
  return months.map((m) => {
    const match = MONTH.exec(m);
    if (!match) return m;
    const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1));
    const month = date.toLocaleDateString("en-US", { month: "short", timeZone: "UTC" });
    return years.size > 1 ? `${month} '${match[1].slice(2)}` : month;
  });
}

/**
 * A card date: ISO dates become "Dec 9, 2026"; free text from the model
 * ("next Tuesday") passes through rather than becoming "Invalid Date".
 */
export function formatCardDate(value: string | null | undefined): string {
  if (!value) return "";
  if (!/^\d{4}-\d{2}-\d{2}/.test(value)) return value;
  // A bare date is a calendar day, not a UTC midnight that renders as the day before in the Americas.
  const isoDay = /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T12:00:00` : value;
  const formatted = formatDate(isoDay);
  return formatted === "Invalid Date" ? value : formatted;
}

/** Whole-percent discount between a price and the price it replaced; 0 when there is none. */
export function discountPercent(price: number | undefined, originalPrice: number | undefined): number {
  if (price == null || originalPrice == null || originalPrice <= price || originalPrice <= 0) return 0;
  return Math.round((1 - price / originalPrice) * 100);
}
