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
          return <ChatProductCard key={i} data={seg.data as any} />;
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

interface Segment {
  type: string;
  text: string;
  data?: Record<string, unknown>;
}

// UUID pattern
const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;

// Detect order summary blocks in plain text
function detectOrderInText(text: string): { id: string; status?: string; total?: number; tracking?: string } | null {
  // Look for "Order ID: <uuid>" or "(Order ID: <uuid>)" patterns
  const orderIdMatch = text.match(/Order\s*(?:ID|#)?[:\s]*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
  if (!orderIdMatch) return null;

  const id = orderIdMatch[1];

  // Try to extract status
  const statusMatch = text.match(/Status[:\s]*(\w[\w\s]*?)(?:\n|$|\()/i);
  const status = statusMatch ? statusMatch[1].trim().toLowerCase().replace(/\s+/g, "_") : undefined;

  // Try to extract total
  const totalMatch = text.match(/Total[:\s]*\$?([\d,]+\.?\d*)/i);
  const total = totalMatch ? parseFloat(totalMatch[1].replace(",", "")) : undefined;

  // Try to extract tracking
  const trackingMatch = text.match(/Tracking\s*(?:Number)?[:\s]*(TRK\w+|\w{10,})/i);
  const tracking = trackingMatch ? trackingMatch[1] : undefined;

  return { id, status, total, tracking };
}

// Detect product mentions with prices
function detectProductsInText(text: string): { name: string; price?: number; rating?: number; category?: string }[] {
  const products: { name: string; price?: number; rating?: number; category?: string }[] = [];

  // Pattern: "**Product Name** — description. Price: $XX.XX" or "Product Name — $XX.XX"
  // Also: "- Product Name ($XX.XX)" or "Product Name – $XX.XX | Rating: X.X"
  const lines = text.split("\n");
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.length < 10) continue;

    // Skip header/label lines
    if (/^(Order|Status|Total|Shipping|Tracking|Address|Date|Hi |Would|If |Products in)/i.test(trimmed)) continue;

    // Find any $ price in the line
    const priceMatch = trimmed.match(/\$(\d[\d,]*\.?\d*)/);
    if (!priceMatch) continue;

    const price = parseFloat(priceMatch[1].replace(",", ""));

    // Extract product name — everything before the first ( or — or – or $
    let name = trimmed
      .replace(/^[-•*]\s*/, "")        // strip leading bullet
      .replace(/^\d+\.\s*/, "")        // strip "1. "
      .replace(/^\*\*/, "").replace(/\*\*/, "")  // strip bold
      .split(/\s*[—–]\s*|\s*\((?:Electronics|Clothing|Home|Sports|Books)/i)[0]
      .trim();

    // If name still has price in it, cut before $
    if (name.includes("$")) {
      name = name.split("$")[0].trim();
    }

    // Clean trailing punctuation
    name = name.replace(/[,:\s]+$/, "");

    if (name.length < 4 || name.length > 80) continue;
    if (/^(Price|Total|Status|Order|Shipping|Tracking|Would|Hi )/i.test(name)) continue;

    // Extract category
    const catMatch = trimmed.match(/\((Electronics|Clothing|Home|Sports|Books)/i);
    const category = catMatch ? catMatch[1] : undefined;

    // Extract rating
    const ratingMatch = trimmed.match(/Rating[:\s]*(\d\.?\d?)/i) || trimmed.match(/(\d\.\d)\s*(?:\/\s*5|stars?)/i);
    const rating = ratingMatch ? parseFloat(ratingMatch[1]) : undefined;

    products.push({ name, price, rating, category });
  }

  return products;
}

function parseContent(content: string): Segment[] {
  // 1. First check for fenced code blocks (```product, ```order)
  const codeBlockRegex = /```(product|order|products)\n([\s\S]*?)```/g;
  const segments: Segment[] = [];
  let lastIndex = 0;
  let match;
  let hasCodeBlocks = false;

  while ((match = codeBlockRegex.exec(content)) !== null) {
    hasCodeBlocks = true;
    if (match.index > lastIndex) {
      const text = content.slice(lastIndex, match.index).trim();
      if (text) segments.push({ type: "text", text });
    }
    try {
      const data = JSON.parse(match[2]);
      if (match[1] === "products" && Array.isArray(data)) {
        data.forEach((d: Record<string, unknown>) =>
          segments.push({ type: "product", text: "", data: d }),
        );
      } else {
        segments.push({ type: match[1], text: "", data });
      }
    } catch {
      segments.push({ type: "text", text: match[0] });
    }
    lastIndex = match.index + match[0].length;
  }

  if (hasCodeBlocks) {
    if (lastIndex < content.length) {
      const text = content.slice(lastIndex).trim();
      if (text) segments.push({ type: "text", text });
    }
    return segments;
  }

  // 2. No code blocks — try to detect order/product patterns in plain text
  const orderData = detectOrderInText(content);
  if (orderData) {
    // Split: put the order card at the top, then render the rest as markdown
    segments.push({ type: "order", text: "", data: orderData });
    segments.push({ type: "text", text: content });
    return segments;
  }

  // 3. Detect product listings in plain text
  const products = detectProductsInText(content);
  if (products.length >= 2) {
    // Render as markdown first, then product cards below
    segments.push({ type: "text", text: content });
    for (const p of products.slice(0, 8)) {
      segments.push({ type: "product", text: "", data: p });
    }
    return segments;
  }

  // 4. Default: just markdown
  segments.push({ type: "text", text: content });
  return segments;
}
