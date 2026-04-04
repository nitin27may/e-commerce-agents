"use client";

import ReactMarkdown from "react-markdown";
import { ChatProductCard } from "./product-card";
import { ChatOrderCard } from "./order-card";

interface RichMessageProps {
  content: string;
  onAction?: (message: string) => void;
}

export function RichMessage({ content, onAction }: RichMessageProps) {
  // Split content into segments: plain text and structured blocks
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
        // Default: render as markdown
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

// Parse content for ```product, ```order blocks
function parseContent(content: string): Segment[] {
  // Match fenced code blocks: ```product\n{json}\n``` or ```order\n{json}\n```
  const regex = /```(product|order|products)\n([\s\S]*?)```/g;
  const segments: Segment[] = [];
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(content)) !== null) {
    // Add text before this match
    if (match.index > lastIndex) {
      const text = content.slice(lastIndex, match.index).trim();
      if (text) segments.push({ type: "text", text });
    }
    // Try to parse the JSON
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

  // Add remaining text
  if (lastIndex < content.length) {
    const text = content.slice(lastIndex).trim();
    if (text) segments.push({ type: "text", text });
  }

  if (segments.length === 0) {
    segments.push({ type: "text", text: content });
  }

  return segments;
}
