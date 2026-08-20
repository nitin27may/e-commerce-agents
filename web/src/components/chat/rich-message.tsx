"use client";

import ReactMarkdown from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import { motion } from "framer-motion";
import { ChatProductCard } from "./product-card";
import { ChatOrderCard } from "./order-card";
import { ChatCheckoutCard } from "./checkout-card";
import { ChatReturnCard } from "./return-card";
import { ComparisonCard } from "./comparison-card";
import { ChatSentimentCard } from "./sentiment-card";
import { listItem } from "@/lib/motion";
import {
  CardKind,
  validateCard,
} from "@/lib/chat-schemas";

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

function SanitisedMarkdown({ children }: { children: string }) {
  return (
    <ReactMarkdown rehypePlugins={[[rehypeSanitize, SANITIZE_SCHEMA]]}>
      {children}
    </ReactMarkdown>
  );
}

interface RichMessageProps {
  content: string;
  streaming?: boolean;
  onAction?: (message: string) => void;
}

export function RichMessage({ content, streaming, onAction }: RichMessageProps) {
  const processed = streaming ? stripUnclosedFence(content) : content;
  const segments = parseContent(processed);

  return (
    <div className="space-y-3">
      {segments.map((seg, i) => {
        if (seg.type === "product" && seg.data) {
          return (
            <CardMotion key={i}>
              <ChatProductCard data={seg.data as any} onAction={onAction} />
            </CardMotion>
          );
        }
        if (seg.type === "order" && seg.data) {
          return (
            <CardMotion key={i}>
              <ChatOrderCard data={seg.data as any} onAction={onAction} />
            </CardMotion>
          );
        }
        if (seg.type === "checkout" && seg.data) {
          return (
            <CardMotion key={i}>
              <ChatCheckoutCard data={seg.data as any} />
            </CardMotion>
          );
        }
        if (seg.type === "return" && seg.data) {
          return (
            <CardMotion key={i}>
              <ChatReturnCard data={seg.data as any} />
            </CardMotion>
          );
        }
        if (seg.type === "comparison" && seg.data) {
          return (
            <CardMotion key={i}>
              <ComparisonCard products={seg.data as any} onAction={onAction} />
            </CardMotion>
          );
        }
        if (seg.type === "sentiment" && seg.data) {
          return (
            <CardMotion key={i}>
              <ChatSentimentCard data={seg.data as any} />
            </CardMotion>
          );
        }
        return (
          <div
            key={i}
            className="prose prose-sm prose-slate max-w-none prose-p:my-1 prose-li:my-0.5 prose-headings:mt-3 prose-headings:mb-1 prose-ul:my-1 prose-ol:my-1 prose-strong:text-foreground dark:prose-invert"
          >
            <SanitisedMarkdown>{seg.text}</SanitisedMarkdown>
          </div>
        );
      })}
    </div>
  );
}

// ─── Types ────────────────────────────────────────────────────────────

export interface Segment {
  type: string;
  text: string;
  data?: Record<string, unknown>;
}

// ─── Streaming helper: drop a trailing unclosed structured fence ─────
//
// While chunks arrive, a fence like ```product\n{"id":"p1"... may be
// half-written. We don't want raw JSON flickering in the bubble — hide
// everything from the unclosed opener until the stream finishes.

function stripUnclosedFence(content: string): string {
  // `products` listed before `product` so the longer kind wins; no trailing
  // \n requirement — the SSE transport can drop the newline after the fence
  // marker, collapsing ```product\n{...} into ```product{...}.
  const openRegex = /```(products|product|order|checkout|return|sentiment)/g;
  let cursor = 0;
  let lastUnclosedStart = -1;
  let match;
  while ((match = openRegex.exec(content)) !== null) {
    const openStart = match.index;
    const afterOpen = openStart + match[0].length;
    const closeIdx = content.indexOf("```", afterOpen);
    if (closeIdx === -1) {
      lastUnclosedStart = openStart;
      break;
    }
    cursor = closeIdx + 3;
    openRegex.lastIndex = cursor;
  }
  if (lastUnclosedStart === -1) return content;
  return content.slice(0, lastUnclosedStart).trimEnd();
}

// ─── 1. Fenced Code Block Parser (primary path) ──────────────────────

function parseCodeBlocks(content: string): Segment[] | null {
  // The newline after the fence marker is OPTIONAL: the streaming transport
  // frames a lone "\n" LLM token as an empty SSE data line, so it gets dropped
  // and ```product\n{...} arrives as ```product{...}. We tolerate any
  // whitespace (incl. none) and require the body to start with { or [ so a
  // stray ```product-ideas block isn't misread as a card.
  const codeBlockRegex = /```(products|product|order|checkout|return|sentiment)\s*([[{][\s\S]*?)```/g;
  const segments: Segment[] = [];
  let lastIndex = 0;
  let match;
  let found = false;

  while ((match = codeBlockRegex.exec(content)) !== null) {
    found = true;
    if (match.index > lastIndex) {
      const text = content.slice(lastIndex, match.index).trim();
      if (text) segments.push({ type: "text", text });
    }
    try {
      const data = JSON.parse(match[2].trim());
      if (match[1] === "products" && Array.isArray(data)) {
        // Two products → side-by-side comparison card
        if (data.length === 2) {
          const [p1, p2] = data.map((d: unknown) => validateCard("product", d));
          if (p1 && p2) {
            segments.push({ type: "comparison", text: "", data: [p1, p2] as any });
          } else {
            // Fallback: render as individual cards
            data.forEach((d: unknown) => {
              const validated = validateCard("product", d);
              if (validated) segments.push({ type: "product", text: "", data: validated });
            });
          }
        } else {
          data.forEach((d: unknown) => {
            const validated = validateCard("product", d);
            if (validated) {
              segments.push({ type: "product", text: "", data: validated });
            }
          });
        }
      } else {
        const kind = match[1] as CardKind;
        const validated = validateCard(kind, data);
        if (validated) {
          segments.push({ type: kind, text: "", data: validated });
        } else {
          // Schema rejected the payload — drop the fence rather than
          // showing the raw JSON, matching the "products" array branch's
          // existing silent-drop convention above (an item that fails
          // validateCard there is dropped too, not shown raw). The
          // surrounding conversational text is unaffected — it was
          // already captured as its own segment by the lastIndex
          // bookkeeping around this match. Logged so a bad LLM response
          // is still debuggable without being user-visible.
          if (process.env.NODE_ENV !== "production") {
            console.warn(`[rich-message] dropped a \`${kind}\` fence that failed schema validation`, data);
          }
        }
      }
    } catch (err) {
      // Malformed JSON inside a recognized fence tag — same treatment:
      // drop it, don't show the raw (broken) JSON to the user.
      if (process.env.NODE_ENV !== "production") {
        console.warn(`[rich-message] dropped a \`${match[1]}\` fence with malformed JSON`, err);
      }
    }
    lastIndex = match.index + match[0].length;
  }

  if (!found) return null;

  if (lastIndex < content.length) {
    const text = content.slice(lastIndex).trim();
    if (text) segments.push({ type: "text", text });
  }

  return dedupeCards(suppressRedundantText(segments));
}

// Collapse duplicate cards. The orchestrator often restates a specialist's
// product/order data, so the same item can arrive twice — once as a clean
// fenced block (→ card) and once as a collapsed-fence copy (now also a card).
// Keyed by kind + id so duplicates render once.
function dedupeCards(segments: Segment[]): Segment[] {
  const seen = new Set<string>();
  return segments.filter((seg) => {
    let key: string | null = null;
    if (seg.type === "product" && seg.data) {
      const id = (seg.data as { id?: string }).id;
      key = id ? `product:${id}` : null;
    } else if (seg.type === "order" && seg.data) {
      const d = seg.data as { id?: string; order_id?: string };
      const id = d.id ?? d.order_id;
      key = id ? `order:${id}` : null;
    } else if (seg.type === "comparison" && Array.isArray(seg.data)) {
      key =
        "comparison:" +
        (seg.data as Array<{ id?: string }>).map((p) => p?.id ?? "").join(",");
    } else if (seg.type === "checkout" && seg.data) {
      // No id on a checkout — key on the cart shape (the orchestrator often
      // restates the same checkout twice in one message).
      const d = seg.data as { item_count?: number; total?: number };
      key = `checkout:${d.item_count ?? ""}:${d.total ?? ""}`;
    } else if (seg.type === "return" && seg.data) {
      const d = seg.data as { return_id?: string; order_id?: string };
      key = `return:${d.return_id ?? d.order_id ?? ""}`;
    } else if (seg.type === "sentiment" && seg.data) {
      const d = seg.data as { product_id?: string; product_name?: string };
      key = `sentiment:${d.product_id ?? d.product_name ?? ""}`;
    }
    if (key === null) return true;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

// Remove text segments that are plain-text dumps of adjacent structured cards.
function suppressRedundantText(segments: Segment[]): Segment[] {
  const cardTypes = new Set(["order", "product", "products", "checkout", "return"]);
  return segments.filter((seg, i) => {
    if (seg.type !== "text") return true;
    const prev = segments[i - 1];
    const next = segments[i + 1];
    const adjacentToCard =
      (prev && cardTypes.has(prev.type)) ||
      (next && cardTypes.has(next.type));
    return !(adjacentToCard && looksLikeStructuredDump(seg.text));
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
  const orderData = extractOrderData(orderText);
  if (!orderData) return null;

  const segments: Segment[] = [];
  const introText = paragraphs.slice(0, startIdx).join("\n\n").trim();
  if (introText) segments.push({ type: "text", text: introText });
  segments.push({ type: "order", text: "", data: orderData as any });
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
    const product = tryParseProductParagraph(para.trim());
    if (product) {
      productCount++;
      segments.push({ type: "product", text: "", data: product as any });
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
// parseCodeBlocks only recognizes the 5 known card tags. Every real
// backend-emitted fence tag matches one of those today (verified against
// every prompt YAML that emits a fence), so this is defense against a
// tag this parser was never taught — an LLM hallucinating a wrong tag
// name, or a future card type added to the backend before this file is
// updated for it — not a currently-reachable path. Without this, an
// unrecognized tag falls through untouched to ReactMarkdown, which
// treats any ``` fence as a generic code block and renders the raw JSON
// verbatim: exactly the "never show raw JSON" failure this component
// exists to prevent for the 5 known tags.
const KNOWN_CARD_TAGS = new Set(["products", "product", "order", "checkout", "return", "sentiment"]);
const ANY_FENCE_RE = /```([a-zA-Z0-9_-]*)\s*([[{][\s\S]*?)```/g;

function stripUnrecognizedJsonFences(content: string): string {
  return content.replace(ANY_FENCE_RE, (full, tag: string) => {
    if (KNOWN_CARD_TAGS.has(tag)) return full; // parseCodeBlocks handles these
    if (process.env.NODE_ENV !== "production") {
      console.warn(`[rich-message] dropped an unrecognized \`${tag || "(untagged)"}\` JSON-shaped fence`);
    }
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
