/**
 * Demo scenario registry — the 8 scenarios from the README surfaced as
 * one-click launchers on the landing page, home dashboard, and Cmd-K palette.
 *
 * Each scenario deep-links into chat with the prompt prefilled so a recruiter
 * can run through the entire multi-agent flow in under a minute.
 */

import {
  Search,
  GitCompare,
  Package,
  RotateCcw,
  TrendingDown,
  Star,
  Warehouse,
  Shuffle,
  type LucideIcon,
} from "lucide-react";

export interface Scenario {
  id: string;
  label: string;
  /** One-line description of what this demonstrates. */
  description: string;
  /** Full prompt sent to chat. */
  prompt: string;
  icon: LucideIcon;
}

export const DEMO_SCENARIOS: Scenario[] = [
  {
    id: "product-search",
    label: "Product Search",
    description: "Find products by what you need and what you want to spend",
    prompt: "Find me wireless headphones under $300 with good noise cancellation",
    icon: Search,
  },
  {
    id: "comparison",
    label: "Side-by-Side Comparison",
    description: "Two products side by side: specs, price and reviews",
    prompt: "Compare the Sony WH-1000XM5 with AirPods Max",
    icon: GitCompare,
  },
  {
    id: "order-tracking",
    label: "Order Tracking",
    description: "Where your order is and when it arrives",
    prompt: "Where's my latest order?",
    icon: Package,
  },
  {
    id: "return-flow",
    label: "Return Flow",
    description: "Check whether an order can go back, and start the return",
    prompt: "I want to return my last order",
    icon: RotateCcw,
  },
  {
    id: "price-check",
    label: "Price Intelligence",
    description: "Whether now is a good time to buy",
    prompt: "Is the Logitech MX Master 3S a good deal right now?",
    icon: TrendingDown,
  },
  {
    id: "review-analysis",
    label: "Review Analysis",
    description: "What buyers actually say, in one summary",
    prompt: "What do people think about the Dyson V15?",
    icon: Star,
  },
  {
    id: "stock-check",
    label: "Stock & Fulfillment",
    description: "In stock or not, and how fast it can ship",
    prompt: "Is the Dyson V15 Detect in stock?",
    icon: Warehouse,
  },
  {
    id: "multi-intent",
    label: "Multi-Intent",
    description: "Several questions in one message",
    prompt: "Return my jacket and find me a warmer one under $200",
    icon: Shuffle,
  },
];

/**
 * The first four scenarios as plain label/prompt pairs — used by the quick-
 * prompt chip row on the home dashboard (backward-compatible shape).
 */
export const QUICK_PROMPTS = DEMO_SCENARIOS.slice(0, 4).map(({ label, prompt }) => ({
  label,
  prompt,
}));

/** Build a chat deep-link that prefills the composer with a prompt. */
export function chatPromptHref(prompt: string): string {
  return `/chat?prompt=${encodeURIComponent(prompt)}`;
}

/** Build a public assistant deep-link (no auth required). */
export function shopAssistantHref(prompt: string): string {
  return `/shop/assistant?prompt=${encodeURIComponent(prompt)}`;
}
