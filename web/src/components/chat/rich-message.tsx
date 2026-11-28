"use client";

import type { ComponentProps } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import { motion } from "framer-motion";
import { listItem } from "@/lib/motion";
import {
  cardDedupeKey,
  cardFacts,
  cardFenceOpenRegex,
  isCardFenceTag,
  scanCardFences,
  validateCard,
  warnDropped,
  type Card,
  type CardData,
  type CardKind,
} from "@/lib/chat-schemas";
import { CARD_COMPONENTS } from "./card-registry";

const CardMotion = ({ children }: { children: React.ReactNode }) => (
  <motion.div variants={listItem} initial="hidden" animate="visible">
    {children}
  </motion.div>
);

// rehype-sanitize's default schema strips `<script>`, on* attributes,
// `javascript:` URLs and similar. We allow class names so the prose
// styling on `<code>` / `<pre>` / `<table>` keeps working — that's the
// only deviation from the default.
const SANITIZE_SCHEMA = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [...(defaultSchema.attributes?.code ?? []), "className"],
  },
};

// Element styles for answer text. The `prose` classes this used to rely on come from
// @tailwindcss/typography, which was never installed, so lists rendered without bullets and
// headings, tables and code as plain text. Explicit classes keep the chat's own type scale.
const MARKDOWN_COMPONENTS: ComponentProps<typeof ReactMarkdown>["components"] = {
  p: (props) => <p className="my-1 leading-relaxed" {...props} />,
  ul: (props) => <ul className="my-1 list-disc space-y-0.5 pl-5" {...props} />,
  ol: (props) => <ol className="my-1 list-decimal space-y-0.5 pl-5" {...props} />,
  li: (props) => <li className="pl-0.5 marker:text-muted-foreground" {...props} />,
  strong: (props) => <strong className="font-semibold text-foreground" {...props} />,
  h1: (props) => <h3 className="mt-3 mb-1 text-base font-semibold" {...props} />,
  h2: (props) => <h3 className="mt-3 mb-1 text-base font-semibold" {...props} />,
  h3: (props) => <h4 className="mt-3 mb-1 text-sm font-semibold" {...props} />,
  a: (props) => <a className="text-primary underline underline-offset-2" target="_blank" rel="noopener noreferrer" {...props} />,
  code: (props) => <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]" {...props} />,
  table: (props) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-sm" {...props} />
    </div>
  ),
  th: (props) => <th className="border-b px-2 py-1 text-left font-medium" {...props} />,
  td: (props) => <td className="border-b px-2 py-1" {...props} />,
};

function SanitisedMarkdown({ children }: { children: string }) {
  return (
    <ReactMarkdown rehypePlugins={[[rehypeSanitize, SANITIZE_SCHEMA]]} components={MARKDOWN_COMPONENTS}>
      {children}
    </ReactMarkdown>
  );
}

interface RichMessageProps {
  content: string;
  streaming?: boolean;
  onAction?: (message: string) => void;
}

function CardView({ card, onAction }: { card: Card; onAction?: (message: string) => void }) {
  // CARD_COMPONENTS is keyed by the same kind as `card`, so the pairing is sound;
  // TypeScript cannot correlate the two through a union, hence the widening.
  const Component = CARD_COMPONENTS[card.kind] as React.ComponentType<{
    data: Card["data"];
    onAction?: (message: string) => void;
  }>;
  return <Component data={card.data} onAction={onAction} />;
}

/** Holds the place of a card whose fence is still streaming, so the answer does not jump when it lands. */
function CardSkeleton() {
  return (
    <div
      data-testid="card-skeleton"
      aria-hidden
      className="h-28 w-full max-w-lg animate-pulse rounded-xl border border-border bg-card"
    />
  );
}

export function RichMessage({ content, streaming, onAction }: RichMessageProps) {
  const { content: visible, pending } = streaming ? stripUnclosedFence(content) : { content, pending: false };
  const segments = parseContent(visible);

  return (
    <div className="space-y-3">
      {segments.map((seg, i) =>
        seg.type === "text" ? (
          <div key={i} className="max-w-none" data-testid="assistant-text">
            <SanitisedMarkdown>{seg.text}</SanitisedMarkdown>
          </div>
        ) : (
          <CardMotion key={i}>
            <CardView card={segmentCard(seg)} onAction={onAction} />
          </CardMotion>
        ),
      )}
      {pending && <CardSkeleton />}
    </div>
  );
}

// ─── Types ────────────────────────────────────────────────────────────

/**
 * One piece of a parsed message: markdown text, or a validated card whose
 * `type` is its kind in lib/chat-schemas.ts's CARD_REGISTRY.
 */
export type Segment =
  | { type: "text"; text: string; data?: undefined }
  | { [K in CardKind]: { type: K; data: CardData<K> } }[CardKind];

type CardSegment = Exclude<Segment, { type: "text" }>;

const segmentCard = (seg: CardSegment): Card => ({ kind: seg.type, data: seg.data }) as Card;
const cardSegment = (card: Card): CardSegment => ({ type: card.kind, data: card.data }) as CardSegment;

// ─── Streaming helper: hide a trailing unclosed card fence ────────────
//
// While chunks arrive, a fence like ```product\n{"id":"p1"... may be
// half-written. Raw JSON must not flicker in the message, so everything from
// the unclosed opener on is hidden until the stream finishes, and the caller
// shows a skeleton in its place.

export function stripUnclosedFence(content: string): { content: string; pending: boolean } {
  const openRegex = cardFenceOpenRegex();
  let match;
  while ((match = openRegex.exec(content)) !== null) {
    const closeIdx = content.indexOf("```", match.index + match[0].length);
    if (closeIdx === -1) return { content: content.slice(0, match.index).trimEnd(), pending: true };
    openRegex.lastIndex = closeIdx + 3;
  }
  return { content, pending: false };
}

// ─── 1. Card fences (primary path) ────────────────────────────────────

function parseCodeBlocks(content: string): Segment[] | null {
  const fences = scanCardFences(content);
  if (fences.length === 0) return null;

  // A fence that failed validation contributes no card; the text around it
  // survives as its own segment, so nothing raw is ever shown.
  const segments: Segment[] = [];
  let cursor = 0;
  for (const fence of fences) {
    const before = content.slice(cursor, fence.start).trim();
    if (before) segments.push({ type: "text", text: before });
    segments.push(...fence.cards.map(cardSegment));
    cursor = fence.end;
  }
  const after = content.slice(cursor).trim();
  if (after) segments.push({ type: "text", text: after });

  return dedupeCards(suppressRedundantText(segments));
}

// The orchestrator often restates a specialist's card, so the same item can
// arrive twice in one message. Keyed by kind + id (CARD_REGISTRY's dedupeKey).
function dedupeCards(segments: Segment[]): Segment[] {
  const seen = new Set<string>();
  return segments.filter((seg) => {
    if (seg.type === "text") return true;
    const key = cardDedupeKey(segmentCard(seg));
    if (key === null) return true;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

const LIST_ITEM = /^\s*(?:[-*•+]|\d+[.)])\s+/;

/**
 * Drops the bullet list at one edge of `text` (the end, when the card follows;
 * the start, when it precedes) if most of its items repeat a value the card
 * already shows. Lists elsewhere in the text are analysis, not restatement, and stay.
 */
export function dropRestatedList(text: string, facts: string[], edge: "start" | "end"): string {
  if (facts.length === 0) return text;
  const lines = text.split("\n");
  const step = edge === "start" ? 1 : -1;
  let i = edge === "start" ? 0 : lines.length - 1;
  while (i >= 0 && i < lines.length && lines[i].trim() === "") i += step;
  const first = i;
  while (i >= 0 && i < lines.length && LIST_ITEM.test(lines[i])) i += step;
  const items = edge === "start" ? lines.slice(first, i) : lines.slice(i + 1, first + 1);
  if (items.length === 0) return text;

  const needles = facts.map((f) => f.toLowerCase());
  const restated = items.filter((item) => needles.some((n) => item.toLowerCase().includes(n))).length;
  if (restated * 2 <= items.length) return text;

  return (edge === "start" ? lines.slice(i) : lines.slice(0, i + 1)).join("\n");
}

// Remove text that only restates an adjacent card: a field-by-field dump, or a
// bullet list of the card's own prices and names.
function suppressRedundantText(segments: Segment[]): Segment[] {
  return segments.flatMap((seg, i): Segment[] => {
    if (seg.type !== "text") return [seg];
    const prev = segments[i - 1];
    const next = segments[i + 1];
    const prevCard = prev && prev.type !== "text" ? segmentCard(prev) : null;
    const nextCard = next && next.type !== "text" ? segmentCard(next) : null;
    if (!prevCard && !nextCard) return [seg];
    if (looksLikeStructuredDump(seg.text)) return [];

    let text = seg.text;
    if (nextCard) text = dropRestatedList(text, cardFacts(nextCard), "end");
    if (prevCard) text = dropRestatedList(text, cardFacts(prevCard), "start");
    text = text.trim();
    return text ? [{ type: "text", text }] : [];
  });
}

function looksLikeStructuredDump(text: string): boolean {
  // Line-prefix tolerant: lines may start with "- ", "* ", bullets, or nothing.
  // LINE = start-of-line, optional bullet/dash, optional whitespace.
  const LINE = "^[\\s]*[-*•]?[\\s]*";
  const patterns = [
    new RegExp(`${LINE}Status[:\\s]`, "im"),
    new RegExp(`${LINE}Order\\s*(Total|ID|#|Date|Placed)[:\\s]`, "im"),
    new RegExp(`${LINE}Tracking(\\s*Number)?[:\\s]`, "im"),
    new RegExp(`${LINE}(Shipping\\s*(Carrier|Address)|Carrier|Address)[:\\s]`, "im"),
    new RegExp(`${LINE}(Total|Subtotal|Amount)[:\\s]*\\$`, "im"),
    new RegExp(`${LINE}(Date|Placed)[:\\s]*\\d{4}-\\d{2}-\\d{2}`, "im"),
    /Items?\s*(in (?:this|your) order|:)/i,
    /[—–-]\s*\d+\s*[×x]\s*\$/m,
    /\(\s*\d+\s*[×x]\s*\$[\d,.]+\s*\)/, // "(1 × $189.99)"
    /Order\s*(Status\s*)?Timeline/i,
    new RegExp(`${LINE}Price[:\\s]*\\$`, "im"),
    new RegExp(`${LINE}Rating[:\\s]`, "im"),
  ];
  return patterns.filter((p) => p.test(text)).length >= 3;
}

// ─── 2. Order Text Detection (fallback) ──────────────────────────────

interface OrderItem {
  name: string;
  quantity: number;
  unit_price: number;
  total?: number;
  category?: string;
  brand?: string;
}

interface TimelineEvent {
  status: string;
  date: string;
}

function parseOrderInText(content: string): Segment[] | null {
  // Must have a UUID in an "Order" context
  if (
    !/Order\s*(?:ID|#)?[:\s]*[0-9a-f]{8}-/i.test(content)
  )
    return null;

  const paragraphs = content.split(/\n\n+/);

  const isOrderParagraph = (para: string): boolean => {
    const t = para.trim();
    return (
      /^Order\s*(Summary|ID|#)/im.test(t) ||
      /^Status[:\s]/im.test(t) ||
      /^Total[:\s]*\$/im.test(t) ||
      /(?:Shipping|Tracking|Carrier|Address|Placed)[:\s]/im.test(t) ||
      /^Items?\s*(in|:|\()/im.test(t) ||
      /[—–]\s*\d+\s*[×x]\s*\$/m.test(t) ||
      /^Order\s*Status\s*Timeline/im.test(t) ||
      /^\d{4}-\d{2}-\d{2}[:\s]/m.test(t)
    );
  };

  // Find first and last order-related paragraph
  let startIdx = -1;
  let endIdx = -1;
  for (let i = 0; i < paragraphs.length; i++) {
    if (isOrderParagraph(paragraphs[i])) {
      if (startIdx === -1) startIdx = i;
      endIdx = i;
    } else if (startIdx !== -1) {
      // Stop at first non-order paragraph after the order block
      break;
    }
  }
  if (startIdx === -1) return null;

  // Merge all order paragraphs and extract structured data
  const orderText = paragraphs.slice(startIdx, endIdx + 1).join("\n\n");
  const orderData = validateCard("order", extractOrderData(orderText));
  if (!orderData) return null;

  const segments: Segment[] = [];
  const introText = paragraphs.slice(0, startIdx).join("\n\n").trim();
  if (introText) segments.push({ type: "text", text: introText });
  segments.push({ type: "order", data: orderData });
  const outroText = paragraphs.slice(endIdx + 1).join("\n\n").trim();
  if (outroText) segments.push({ type: "text", text: outroText });
  return segments;
}

function extractOrderData(
  text: string
): Record<string, unknown> | null {
  const idMatch = text.match(
    /Order\s*(?:ID|#)?[:\s]*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i
  );
  if (!idMatch) return null;

  const id = idMatch[1];

  // Use line-anchored regexes to avoid matching stray words
  const statusMatch = text.match(/^Status[:\s]+(\w[\w\s]*?)$/im);
  const status = statusMatch
    ? statusMatch[1].trim().toLowerCase().replace(/\s+/g, "_")
    : undefined;

  const totalMatch = text.match(/Total[:\s]*\$?([\d,]+\.?\d*)/i);
  const total = totalMatch
    ? parseFloat(totalMatch[1].replace(",", ""))
    : undefined;

  const trackingMatch = text.match(
    /Tracking\s*(?:Number)?[:\s]*(TRK\w+|\w{10,})/i
  );
  const tracking = trackingMatch ? trackingMatch[1] : undefined;

  const carrierMatch = text.match(
    /(?:Shipping\s*)?Carrier[:\s]*(.+?)(?:\n|$)/i
  );
  const carrier = carrierMatch ? carrierMatch[1].trim() : undefined;

  const addressMatch = text.match(
    /(?:Shipping\s*)?Address[:\s]*(.+?)(?:\n|$)/i
  );
  const shipping_address = addressMatch
    ? addressMatch[1].trim()
    : undefined;

  const dateMatch = text.match(
    /(?:Order\s*)?(?:Placed|Date)[:\s]*(.+?)(?:\n|$)/i
  );
  const date = dateMatch ? dateMatch[1].trim() : undefined;

  // Parse items: "Product Name (Brand, Category) — Qty × $Price = $Total"
  const items: OrderItem[] = [];
  const itemRegex =
    /^[-•*]?\s*(.+?)\s*\(([^)]+)\)\s*[—–-]\s*(\d+)\s*[×x]\s*\$([\d,.]+)(?:\s*=\s*\$([\d,.]+))?/gm;
  let m;
  while ((m = itemRegex.exec(text)) !== null) {
    const parts = m[2].split(/,\s*/);
    const unitPrice = parseFloat(m[4].replace(",", ""));
    const qty = parseInt(m[3]);
    items.push({
      name: m[1].trim(),
      quantity: qty,
      unit_price: unitPrice,
      total: m[5] ? parseFloat(m[5].replace(",", "")) : unitPrice * qty,
      brand: parts[0]?.trim() || undefined,
      category: parts[1]?.trim() || undefined,
    });
  }

  // Parse timeline: "2026-03-25: Order placed"
  const timeline: TimelineEvent[] = [];
  const tlRegex = /^(\d{4}-\d{2}-\d{2})[:\s]+(.+?)$/gm;
  while ((m = tlRegex.exec(text)) !== null) {
    timeline.push({ status: m[2].trim(), date: m[1] });
  }

  return {
    id,
    status,
    total,
    tracking,
    carrier,
    shipping_address,
    date,
    items: items.length > 0 ? items : undefined,
    item_count: items.length || undefined,
    timeline: timeline.length > 0 ? timeline : undefined,
  };
}

// ─── 3. Product Text Detection (fallback) ────────────────────────────

function parseProductsInText(content: string): Segment[] | null {
  const paragraphs = content.split(/\n\n+/);
  if (paragraphs.length < 2) return null;

  const segments: Segment[] = [];
  let productCount = 0;

  for (const para of paragraphs) {
    const parsed = tryParseProductParagraph(para.trim());
    const product = parsed && validateCard("product", parsed);
    if (product) {
      productCount++;
      segments.push({ type: "product", data: product });
    } else {
      const text = para.trim();
      if (text) segments.push({ type: "text", text });
    }
  }

  if (productCount === 0) return null;
  return segments;
}

function tryParseProductParagraph(
  para: string
): Record<string, unknown> | null {
  const lines = para
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length < 2) return null;

  // First line = product name
  let nameLine = lines[0]
    .replace(/^\*\*/, "")
    .replace(/\*\*$/, "")
    .replace(/^[-•*]\s*/, "")
    .replace(/^\d+\.\s*/, "")
    .trim();

  if (nameLine.length < 3 || nameLine.length > 100) return null;
  // Skip conversational/sentence lines
  if (
    /^(Hi |Hello|Would|Here |I |Let me|Based on|If you|These |This |For |Check|You |We |Our |Sure|Of course|Great|Thank)/i.test(
      nameLine
    )
  )
    return null;
  if (nameLine.endsWith("?") || nameLine.endsWith("!")) return null;

  // Must have a "Price:" line somewhere in the block
  const hasPrice = lines.some((l) => /Price[:\s]*\$/i.test(l));
  const hasDollar = lines.some((l) => /\$\d/.test(l));
  const hasRating = lines.some((l) => /Rating[:\s]/i.test(l));

  if (!hasPrice && !(hasDollar && hasRating)) return null;

  let price: number | undefined;
  let original_price: number | undefined;
  let rating: number | undefined;
  let review_count: number | undefined;
  let description: string | undefined;
  let brand: string | undefined;
  let category: string | undefined;

  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];

    // Price: $299.99 (was $349.99)
    const priceMatch = line.match(/Price[:\s]*\$([\d,.]+)/i);
    if (priceMatch) {
      price = parseFloat(priceMatch[1].replace(",", ""));
      const wasMatch = line.match(/was\s*\$([\d,.]+)/i);
      if (wasMatch)
        original_price = parseFloat(wasMatch[1].replace(",", ""));
    }

    // Rating: 4.7/5 (15 reviews)
    const ratingMatch = line.match(/Rating[:\s]*([\d.]+)\s*\/\s*5/i);
    if (ratingMatch) {
      rating = parseFloat(ratingMatch[1]);
      const revMatch = line.match(/\((\d+)\s*reviews?\)/i);
      if (revMatch) review_count = parseInt(revMatch[1]);
    }

    // Features: ...
    if (/^Features?[:\s]/i.test(line)) {
      description = line.replace(/^Features?[:\s]*/i, "").trim();
    }

    // Category / Brand explicit lines
    if (/^Category[:\s]/i.test(line))
      category = line.replace(/^Category[:\s]*/i, "").trim();
    if (/^Brand[:\s]/i.test(line))
      brand = line.replace(/^Brand[:\s]*/i, "").trim();

    // "Why it's great:" — append to description
    if (/^Why\s/i.test(line)) {
      const extra = line.replace(/^Why\s+\S+\s*[:.]?\s*/i, "").trim();
      description = description ? `${description}. ${extra}` : extra;
    }
  }

  if (price === undefined) return null;

  // Extract brand from name: "AirPods Max (Apple)"
  const brandInName = nameLine.match(/\((\w[\w\s]*)\)$/);
  if (brandInName && !brand) {
    brand = brandInName[1].trim();
    nameLine = nameLine.replace(/\s*\(\w[\w\s]*\)$/, "").trim();
  }

  return {
    name: nameLine,
    price,
    original_price,
    rating,
    review_count,
    category,
    brand,
    description,
    on_sale: original_price != null && original_price > price,
  };
}

// ─── Guard: strip any JSON-shaped fence this parser doesn't know about ──
//
// A tag outside CARD_REGISTRY (a model hallucinating a tag, or a backend card
// type this file has not caught up with) would otherwise fall through to
// ReactMarkdown, which renders any ``` fence as a code block: raw JSON on
// screen, exactly what this component exists to prevent.

const ANY_FENCE_RE = /```([a-zA-Z0-9_-]*)\s*([[{][\s\S]*?)```/g;

function stripUnrecognizedJsonFences(content: string): string {
  return content.replace(ANY_FENCE_RE, (full, tag: string) => {
    if (isCardFenceTag(tag)) return full;
    warnDropped(`dropped an unrecognized \`${tag || "(untagged)"}\` JSON-shaped fence`);
    return "";
  });
}

// ─── Main Parser ──────────────────────────────────────────────────────

export function parseContent(rawContent: string): Segment[] {
  const content = stripUnrecognizedJsonFences(rawContent);

  // 1. Fenced code blocks (highest priority, most reliable)
  const codeBlockResult = parseCodeBlocks(content);
  if (codeBlockResult) return codeBlockResult;

  // 2. Order block with items in plain text
  const orderResult = parseOrderInText(content);
  if (orderResult) return orderResult;

  // 3. Product blocks in plain text
  const productResult = parseProductsInText(content);
  if (productResult) return productResult;

  // 4. Default: just markdown
  return [{ type: "text", text: content }];
}
