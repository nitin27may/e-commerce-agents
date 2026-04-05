"use client";

import ReactMarkdown from "react-markdown";
import { ChatProductCard } from "./product-card";
import { ChatOrderCard } from "./order-card";

interface RichMessageProps {
  content: string;
  onAction?: (message: string) => void;
}

export function RichMessage({ content, onAction }: RichMessageProps) {
  const segments = parseContent(content);

  return (
    <div className="space-y-3">
      {segments.map((seg, i) => {
        if (seg.type === "product" && seg.data) {
          return (
            <ChatProductCard
              key={i}
              data={seg.data as any}
              onAction={onAction}
            />
          );
        }
        if (seg.type === "order" && seg.data) {
          return <ChatOrderCard key={i} data={seg.data as any} />;
        }
        return (
          <div
            key={i}
            className="prose prose-sm prose-slate max-w-none prose-p:my-1 prose-li:my-0.5 prose-headings:mt-3 prose-headings:mb-1 prose-ul:my-1 prose-ol:my-1 prose-strong:text-slate-800"
          >
            <ReactMarkdown>{seg.text}</ReactMarkdown>
          </div>
        );
      })}
    </div>
  );
}

// ─── Types ────────────────────────────────────────────────────────────

interface Segment {
  type: string;
  text: string;
  data?: Record<string, unknown>;
}

// ─── 1. Fenced Code Block Parser (primary path) ──────────────────────

function parseCodeBlocks(content: string): Segment[] | null {
  const codeBlockRegex = /```(product|order|products)\n([\s\S]*?)```/g;
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
      const data = JSON.parse(match[2]);
      if (match[1] === "products" && Array.isArray(data)) {
        data.forEach((d: Record<string, unknown>) =>
          segments.push({ type: "product", text: "", data: d })
        );
      } else {
        segments.push({ type: match[1], text: "", data });
      }
    } catch {
      segments.push({ type: "text", text: match[0] });
    }
    lastIndex = match.index + match[0].length;
  }

  if (!found) return null;

  if (lastIndex < content.length) {
    const text = content.slice(lastIndex).trim();
    if (text) segments.push({ type: "text", text });
  }
  return segments;
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

// ─── Main Parser ──────────────────────────────────────────────────────

function parseContent(content: string): Segment[] {
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
