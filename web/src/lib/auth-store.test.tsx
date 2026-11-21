import { act } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { AuthProvider, useAuth } from "./auth-context";
import { authStore } from "./auth-store";
import { CartProvider, useCart } from "./cart-context";
import { cartStore } from "./cart-store";

const USER = { email: "a@example.com", name: "Ada", role: "customer" };

function Probe() {
  const { user, isLoading, isAuthenticated } = useAuth();
  const { itemCount } = useCart();
  return (
    <p data-testid="probe">
      {isLoading ? "loading" : isAuthenticated ? `user:${user?.email}` : "anonymous"}|cart:{itemCount}
    </p>
  );
}

function Tree() {
  return (
    <AuthProvider>
      <CartProvider>
        <Probe />
      </CartProvider>
    </AuthProvider>
  );
}

function seedSession() {
  localStorage.setItem("ecommerce_user", JSON.stringify(USER));
  localStorage.setItem("ecommerce_access_token", "access-1");
  localStorage.setItem("ecommerce_refresh_token", "refresh-1");
}

beforeEach(() => {
  localStorage.clear();
  authStore.resetForTests();
  cartStore.resetForTests();
  api.setToken(null);
  api.setRefreshToken(null);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("auth + cart stores: SSR and hydration", () => {
  it("renders the server snapshot (loading, signed out) even when a session is stored", () => {
    seedSession();
    // No `window` reads happen on the server path: the server snapshot is a constant.
    const html = renderToString(<Tree />);
    expect(html.replace(/<!-- -->/g, "")).toContain("loading|cart:0");
    expect(html).not.toContain("a@example.com");
  });

  it("hydrates a stored session without a mismatch, then shows the user and loads the cart", async () => {
    seedSession();
    const serverHtml = renderToString(<Tree />);

    const getCart = vi.spyOn(api, "getCart").mockResolvedValue({
      id: "c1",
      items: [],
      item_count: 3,
      subtotal: 0,
      discount_amount: 0,
      coupon_code: null,
      total: 0,
      shipping_address: null,
      billing_address: null,
      billing_same_as_shipping: true,
    });
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});

    const container = document.createElement("div");
    container.innerHTML = serverHtml;
    document.body.appendChild(container);

    let root!: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(container, <Tree />);
    });

    // React reports a hydration mismatch through console.error.
    expect(errors).not.toHaveBeenCalled();
    expect(container.textContent).toBe("user:a@example.com|cart:3");
    expect(getCart).toHaveBeenCalledTimes(1);
    // The API client picked the stored tokens up, so the first request is authenticated.
    expect(api.getToken()).toBe("access-1");

    await act(async () => root.unmount());
    container.remove();
  });

  it("hydrates a signed-out visitor to anonymous and never loads a cart", async () => {
    const serverHtml = renderToString(<Tree />);
    const getCart = vi.spyOn(api, "getCart");
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});

    const container = document.createElement("div");
    container.innerHTML = serverHtml;
    document.body.appendChild(container);

    let root!: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(container, <Tree />);
    });

    expect(errors).not.toHaveBeenCalled();
    expect(container.textContent).toBe("anonymous|cart:0");
    expect(getCart).not.toHaveBeenCalled();

    await act(async () => root.unmount());
    container.remove();
  });
});

describe("auth store", () => {
  it("persists on login and clears everything on logout", async () => {
    vi.spyOn(api, "login").mockResolvedValue({ user: USER, access_token: "a2", refresh_token: "r2" });
    await authStore.login("a@example.com", "pw");
    expect(authStore.getSnapshot()).toEqual({ user: USER, isLoading: false });
    expect(localStorage.getItem("ecommerce_access_token")).toBe("a2");
    expect(localStorage.getItem("ecommerce_refresh_token")).toBe("r2");
    expect(api.getToken()).toBe("a2");

    authStore.logout();
    expect(authStore.getSnapshot()).toEqual({ user: null, isLoading: false });
    expect(localStorage.getItem("ecommerce_user")).toBeNull();
    expect(localStorage.getItem("ecommerce_access_token")).toBeNull();
    expect(api.getToken()).toBeNull();
  });

  it("returns the same snapshot object until something changes", () => {
    seedSession();
    expect(authStore.getSnapshot()).toBe(authStore.getSnapshot());
  });

  it("treats a corrupt stored user as signed out and clears the storage", () => {
    localStorage.setItem("ecommerce_user", "{not json");
    localStorage.setItem("ecommerce_access_token", "x");
    expect(authStore.getSnapshot()).toEqual({ user: null, isLoading: false });
    expect(localStorage.getItem("ecommerce_access_token")).toBeNull();
  });
});
