import type { CardKind } from "./chat-schemas";

/**
 * Follow-up chips derived from the assistant's last message.
 *
 * Issue #4 proposes regex over the assistant's closing question. That works,
 * but there is a stronger signal already on the client: the message's typed
 * generative-UI payload. A ```product fence means the user is looking at
 * products, whatever prose surrounds it — deterministic, testable, and immune
 * to model phrasing or language.
 *
 * So prose parsing is the *secondary* tier here, not the primary one.
 *
 * No LLM call, deliberately. Generating suggestions with a model would add
 * latency and cost to every turn and need an endpoint the frontend-only
 * constraint of #4 forbids. This is a pure function over text the client
 * already has.
 */

export interface Suggestion {
  label: string;
  prompt: string;
}

/** Chips per card kind. Ordered most-likely-first; only the first three show. */
const BY_CARD: Partial<Record<CardKind, Suggestion[]>> = {
  product: [
    { label: "Check stock", prompt: "Is this in stock?" },
    { label: "Show reviews", prompt: "What do the reviews say about this?" },
    { label: "Find similar", prompt: "Show me similar products" },
  ],
  order: [
    { label: "Track this order", prompt: "Where is this order right now?" },
    { label: "Start a return", prompt: "I want to return this order" },
    { label: "Order details", prompt: "Show me the full details of this order" },
  ],
  pricing: [
    { label: "Any better deals?", prompt: "Are there any better deals on this?" },
    { label: "Price history", prompt: "What has the price history been?" },
    { label: "Apply a coupon", prompt: "Do I have any coupons I can use?" },
  ],
  inventory: [
    { label: "Estimate delivery", prompt: "How fast can this be delivered to me?" },
    { label: "Other warehouses", prompt: "Which other warehouses have this?" },
    { label: "Restock date", prompt: "When will this be back in stock?" },
  ],
  sentiment: [
    { label: "Show negative reviews", prompt: "What do the negative reviews say?" },
    { label: "Summarise the pros", prompt: "What do reviewers like most about this?" },
    { label: "Compare sentiment", prompt: "How does this compare to similar products?" },
  ],
  return: [
    { label: "Return status", prompt: "What is the status of my return?" },
    { label: "Refund timing", prompt: "When will I get my refund?" },
    { label: "Replacement options", prompt: "What can I replace this with?" },
  ],
  checkout: [
    { label: "Apply a coupon", prompt: "Apply my best available coupon" },
    { label: "Shipping options", prompt: "What are my shipping options?" },
    { label: "Place the order", prompt: "Go ahead and place the order" },
  ],
};

/** More than one product changes the useful follow-up from "tell me about it" to "compare". */
const MULTI_PRODUCT: Suggestion[] = [
  { label: "Compare these", prompt: "Compare these products for me" },
  { label: "Check stock", prompt: "Which of these are in stock?" },
  { label: "Any deals?", prompt: "Are any of these on offer?" },
];

const FENCE = /```(product|products|order|checkout|return|sentiment|inventory|pricing)\s*\n/g;

/**
 * Card kinds present in a message, in order of appearance, with duplicates.
 *
 * Counts rather than de-duplicates because "two products" and "one product"
 * warrant different follow-ups.
 */
export function cardKindsIn(text: string): string[] {
  const kinds: string[] = [];
  for (const match of text.matchAll(FENCE)) {
    kinds.push(match[1]);
  }
  return kinds;
}

const MAX_LABEL = 42;
const MIN_LABEL = 3;

/** Sentence-cased, punctuation-stripped, length-checked. Null if unusable. */
function cleanCandidate(raw: string): string | null {
  const text = raw
    .replace(/^[\s,;:–—-]+|[\s,;:.!?–—-]+$/g, "")
    .replace(/\s+/g, " ")
    .trim();

  if (text.length < MIN_LABEL || text.length > MAX_LABEL) return null;
  // A candidate containing a fence marker or newline is parser debris.
  if (/[\n`{}[\]]/.test(text)) return null;

  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * Chips from the assistant's closing question.
 *
 * Only the final sentence, and only when it is a question — anything looser
 * turns mid-answer prose into chips, which is how this tier embarrasses you.
 * If it cannot produce at least two clean candidates it returns nothing, so the
 * caller falls through rather than showing one odd chip beside two canned ones.
 */
export function fromClosingQuestion(text: string): Suggestion[] {
  const trimmed = text.trimEnd();
  if (!trimmed.endsWith("?")) return [];

  const lastSentence = trimmed.split(/(?<=[.!?])\s+/).pop() ?? "";
  if (!lastSentence.endsWith("?") || lastSentence.length > 240) return [];

  const body = lastSentence
    .replace(/\?+$/, "")
    .replace(/^(would you like( me)? to|do you want me to|shall i|should i|can i help you)\s+/i, "")
    .trim();

  const parts = body
    .split(/\s*,\s*or\s+|\s+or\s+|\s*,\s*/i)
    .map(cleanCandidate)
    .filter((c): c is string => c !== null);

  const unique = [...new Set(parts)];
  if (unique.length < 2) return [];

  return unique.slice(0, 3).map((label) => ({ label, prompt: label }));
}

/**
 * Follow-up chips for the composer.
 *
 * Three tiers, first non-empty wins, always topped up from `fallback` so the
 * chip row never shrinks and never renders empty.
 *
 * @param lastAssistantMessage The assistant's most recent message text, if any.
 * @param fallback Today's static suggestions — the tier-3 behaviour, unchanged.
 */
export function deriveSuggestions(
  lastAssistantMessage: string | undefined,
  fallback: Suggestion[]
): Suggestion[] {
  const take3 = (list: Suggestion[]): Suggestion[] => {
    const seen = new Set<string>();
    const out: Suggestion[] = [];
    for (const s of [...list, ...fallback]) {
      if (out.length === 3) break;
      if (seen.has(s.label)) continue;
      seen.add(s.label);
      out.push(s);
    }
    return out;
  };

  if (!lastAssistantMessage?.trim()) return take3([]);

  // ── Tier 1: the typed payload ──────────────────────────────────────────
  const kinds = cardKindsIn(lastAssistantMessage);
  if (kinds.length > 0) {
    // A `products` fence is an array, so it is multi by construction; two or
    // more separate `product` fences count the same way.
    const productCount = kinds.filter((k) => k === "product").length;
    const isMultiProduct = kinds.includes("products") || productCount > 1;

    if (isMultiProduct) return take3(MULTI_PRODUCT);

    // Last card wins: it is the one nearest the composer and the one the user
    // is most likely still looking at.
    const last = kinds[kinds.length - 1];
    const key = (last === "products" ? "product" : last) as CardKind;
    const mapped = BY_CARD[key];
    if (mapped) return take3(mapped);
  }

  // ── Tier 2: the closing question ───────────────────────────────────────
  const fromProse = fromClosingQuestion(lastAssistantMessage);
  if (fromProse.length > 0) return take3(fromProse);

  // ── Tier 3: unchanged ──────────────────────────────────────────────────
  return take3([]);
}
