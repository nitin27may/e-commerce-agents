"use client";

import type { ComponentType } from "react";
import type { CardData, CardKind } from "@/lib/chat-schemas";
import { ChatProductCard } from "./product-card";
import { ComparisonCard } from "./comparison-card";
import { ChatOrderCard } from "./order-card";
import { ChatCheckoutCard } from "./checkout-card";
import { ChatReturnCard } from "./return-card";
import { ChatSentimentCard } from "./sentiment-card";
import { ChatInventoryCard } from "./inventory-card";
import { ChatPricingCard } from "./pricing-card";

export interface CardComponentProps<K extends CardKind> {
  data: CardData<K>;
  /** Sends a message on the shopper's behalf (a card action is a reply in the conversation). */
  onAction?: (message: string) => void;
}

/**
 * The component for each card kind in lib/chat-schemas.ts's CARD_REGISTRY.
 * Typed as a mapped type over CardKind, so adding a kind there without a
 * component here is a compile error rather than a silently missing card.
 */
export const CARD_COMPONENTS: { [K in CardKind]: ComponentType<CardComponentProps<K>> } = {
  product: ChatProductCard,
  comparison: ComparisonCard,
  order: ChatOrderCard,
  checkout: ChatCheckoutCard,
  return: ChatReturnCard,
  sentiment: ChatSentimentCard,
  inventory: ChatInventoryCard,
  pricing: ChatPricingCard,
};
