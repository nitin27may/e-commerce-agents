"use client";

import { useState } from "react";
import { AlertCircle, Check, LogIn, ShoppingCart } from "lucide-react";
import { useCart } from "@/lib/cart-context";
import { useAuth } from "@/lib/auth-context";
import { TONE_TEXT_CLASS } from "@/components/ui/status-badge";
import { CardAction } from "./chat-card";

/**
 * Product ids are Postgres UUIDs. The model composing a card is told to copy the
 * real id from the tool result, but it occasionally invents a name-derived slug
 * ("sony-wh1000xm5-001"). Those 404 against /api/cart/items and /products/[id],
 * so a non-UUID is treated as no id: the card still renders, without the
 * actions that are guaranteed to fail.
 */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function validProductId(id: string | undefined): string | undefined {
  return id && UUID_RE.test(id) ? id : undefined;
}

export function productHref(productId: string, isAuthenticated: boolean): string {
  return isAuthenticated ? `/products/${productId}` : `/shop/products/${productId}`;
}

interface AddToCartActionProps {
  productId: string;
  productName?: string;
  onAction?: (message: string) => void;
  className?: string;
}

/** "Add to cart" with optimistic state, or "Sign in to buy" for a signed-out visitor. */
export function AddToCartAction({ productId, productName, onAction, className }: AddToCartActionProps) {
  const { cart, addItem } = useCart();
  const { isAuthenticated } = useAuth();
  const [optimistic, setOptimistic] = useState(false);
  const [error, setError] = useState(false);

  if (!isAuthenticated) {
    return (
      <CardAction icon={LogIn} href="/login" className={className}>
        Sign in to buy
      </CardAction>
    );
  }

  // Derived from cart state so "Added" survives a re-render or a return to the conversation.
  const added = optimistic || !!cart?.items?.some((i) => i.product_id === productId);

  const add = (e: React.MouseEvent) => {
    e.stopPropagation();
    // Optimistic: flip now, so the chat stays responsive while the request runs.
    setError(false);
    setOptimistic(true);
    addItem(productId)
      .then(() => {
        // Mirrors the typed flow: ask the chat for the updated cart, which renders a checkout card.
        if (onAction && productName) onAction(`I just added ${productName} to my cart. Show me my updated cart.`);
      })
      .catch(() => {
        setOptimistic(false);
        setError(true);
        setTimeout(() => setError(false), 2500);
      });
  };

  if (error) {
    return (
      <CardAction icon={AlertCircle} variant="destructive" className={className} onClick={add}>
        Retry
      </CardAction>
    );
  }

  if (added) {
    return (
      <CardAction icon={Check} disabled className={`${TONE_TEXT_CLASS.success} disabled:opacity-100 ${className ?? ""}`}>
        Added
      </CardAction>
    );
  }

  return (
    <CardAction
      icon={ShoppingCart}
      variant="primary"
      className={className}
      onClick={add}
    >
      Add to cart
    </CardAction>
  );
}
