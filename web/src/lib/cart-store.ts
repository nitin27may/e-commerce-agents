import { api, type CartResponse } from "@/lib/api";
import { authStore } from "@/lib/auth-store";

/**
 * Cart store behind `useCart()`, shaped for `useSyncExternalStore`.
 *
 * It follows the auth store: a signed-in session loads the cart, a sign-out
 * clears it. That wiring is attached while at least one component is
 * subscribed, so there is no mount effect calling setState. Server snapshot
 * and the state before the first load are the same value (no cart, not
 * loading), so hydration agrees.
 */

export interface CartState {
  cart: CartResponse | null;
  isLoading: boolean;
}

export const EMPTY_CART_STATE: CartState = Object.freeze({ cart: null, isLoading: false });

let state: CartState = EMPTY_CART_STATE;
const listeners = new Set<() => void>();
let detachAuth: (() => void) | null = null;
let lastAuthed: boolean | null = null;
// Bumped on sign-out so a response that was in flight cannot put a cart back.
let generation = 0;

function set(next: CartState): void {
  state = next;
  listeners.forEach((l) => l());
}

function isAuthed(): boolean {
  return authStore.getSnapshot().user !== null;
}

async function refresh(): Promise<void> {
  if (!isAuthed()) {
    set({ ...state, cart: null });
    return;
  }
  const mine = generation;
  try {
    const data = await api.getCart();
    if (mine === generation) set({ ...state, cart: data });
  } catch {
    // Silently fail: the cart is non-critical.
  }
}

function syncWithAuth(): void {
  const authed = isAuthed();
  if (authed === lastAuthed) return;
  lastAuthed = authed;
  if (authed) {
    set({ ...state, isLoading: true });
    void refresh().finally(() => set({ ...state, isLoading: false }));
  } else {
    generation += 1;
    set({ cart: null, isLoading: false });
  }
}

export const cartStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    if (listeners.size === 1) {
      detachAuth = authStore.subscribe(syncWithAuth);
      syncWithAuth();
    }
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0) {
        detachAuth?.();
        detachAuth = null;
        lastAuthed = null;
      }
    };
  },

  getSnapshot(): CartState {
    return state;
  },

  getServerSnapshot(): CartState {
    return EMPTY_CART_STATE;
  },

  refreshCart: refresh,

  async addItem(productId: string, quantity: number = 1): Promise<void> {
    await api.addToCart(productId, quantity);
    await refresh();
  },

  async updateItem(itemId: string, quantity: number): Promise<void> {
    await api.updateCartItem(itemId, quantity);
    await refresh();
  },

  async removeItem(itemId: string): Promise<void> {
    await api.removeCartItem(itemId);
    await refresh();
  },

  resetForTests(): void {
    state = EMPTY_CART_STATE;
    listeners.clear();
    detachAuth = null;
    lastAuthed = null;
    generation = 0;
  },
};
