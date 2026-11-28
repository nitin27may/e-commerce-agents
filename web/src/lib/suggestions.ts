import { CARD_REGISTRY, cardDedupeKey, scanCardFences, type CardKind, type Suggestion } from "./chat-schemas";

export type { Suggestion };

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

/**
 * Card kinds in a message, in order of appearance, with duplicates.
 *
 * Read through the same scanner and validation the renderer uses, so a chip
 * never follows a card that was not shown: a fence without a newline after its
 * tag counts, an empty or invalid one does not. A two-product `products` fence
 * is one `comparison`.
 *
 * Repeats of the same card (same dedupe key) count once, as they render once;
 * distinct cards of one kind each count, because "two products" and "one
 * product" warrant different follow-ups.
 */
export function cardKindsIn(text: string): CardKind[] {
  const seen = new Set<string>();
  return scanCardFences(text)
    .flatMap((fence) => fence.cards)
    .filter((card) => {
      const key = cardDedupeKey(card);
      if (key === null) return true;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .map((card) => card.kind);
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
    // Several products, or a comparison, call for "compare" chips.
    const isMultiProduct = kinds.includes("comparison") || kinds.filter((k) => k === "product").length > 1;
    if (isMultiProduct) return take3([...CARD_REGISTRY.comparison.suggestions]);

    // Last card wins: it is the one nearest the composer and the one the user
    // is most likely still looking at.
    return take3([...CARD_REGISTRY[kinds[kinds.length - 1]].suggestions]);
  }

  // ── Tier 2: the closing question ───────────────────────────────────────
  const fromProse = fromClosingQuestion(lastAssistantMessage);
  if (fromProse.length > 0) return take3(fromProse);

  // ── Tier 3: unchanged ──────────────────────────────────────────────────
  return take3([]);
}
