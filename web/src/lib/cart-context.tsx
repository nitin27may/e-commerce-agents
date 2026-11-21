"use client";

import {
  createContext,
  useContext,
  useMemo,
  useSyncExternalStore,
} from "react";
import { type CartResponse } from "@/lib/api";
import { cartStore } from "@/lib/cart-store";

interface CartContextValue {
  cart: CartResponse | null;
  itemCount: number;
  isLoading: boolean;
  addItem: (productId: string, quantity?: number) => Promise<void>;
  updateItem: (itemId: string, quantity: number) => Promise<void>;
  removeItem: (itemId: string) => Promise<void>;
  refreshCart: () => Promise<void>;
}

const CartContext = createContext<CartContextValue | null>(null);

export function CartProvider({ children }: { children: React.ReactNode }) {
  const { cart, isLoading } = useSyncExternalStore(
    cartStore.subscribe,
    cartStore.getSnapshot,
    cartStore.getServerSnapshot,
  );

  const value = useMemo<CartContextValue>(
    () => ({
      cart,
      itemCount: cart?.item_count ?? 0,
      isLoading,
      addItem: cartStore.addItem,
      updateItem: cartStore.updateItem,
      removeItem: cartStore.removeItem,
      refreshCart: cartStore.refreshCart,
    }),
    [cart, isLoading],
  );

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartContextValue {
  const ctx = useContext(CartContext);
  if (!ctx) {
    throw new Error("useCart must be used within a CartProvider");
  }
  return ctx;
}
