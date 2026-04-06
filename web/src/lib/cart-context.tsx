"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { api, type CartResponse } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

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
  const { isAuthenticated } = useAuth();
  const [cart, setCart] = useState<CartResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const refreshCart = useCallback(async () => {
    if (!isAuthenticated) {
      setCart(null);
      return;
    }
    try {
      const data = await api.getCart();
      setCart(data);
    } catch {
      // Silently fail — cart is non-critical
    }
  }, [isAuthenticated]);

  // Load cart when auth changes
  useEffect(() => {
    if (isAuthenticated) {
      setIsLoading(true);
      refreshCart().finally(() => setIsLoading(false));
    } else {
      setCart(null);
    }
  }, [isAuthenticated, refreshCart]);

  const addItem = useCallback(
    async (productId: string, quantity: number = 1) => {
      await api.addToCart(productId, quantity);
      await refreshCart();
    },
    [refreshCart]
  );

  const updateItem = useCallback(
    async (itemId: string, quantity: number) => {
      await api.updateCartItem(itemId, quantity);
      await refreshCart();
    },
    [refreshCart]
  );

  const removeItem = useCallback(
    async (itemId: string) => {
      await api.removeCartItem(itemId);
      await refreshCart();
    },
    [refreshCart]
  );

  const itemCount = cart?.item_count ?? 0;

  const value = useMemo<CartContextValue>(
    () => ({
      cart,
      itemCount,
      isLoading,
      addItem,
      updateItem,
      removeItem,
      refreshCart,
    }),
    [cart, itemCount, isLoading, addItem, updateItem, removeItem, refreshCart]
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
