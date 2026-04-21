const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

export interface Address {
  name?: string;
  street: string;
  city: string;
  state: string;
  zip: string;
  country: string;
  phone?: string;
}

export interface CartItem {
  id: string;
  product_id: string;
  name: string;
  brand: string;
  category: string;
  price: number;
  original_price?: number;
  quantity: number;
  subtotal: number;
  image_url?: string;
  in_stock?: boolean;
  available_qty?: number;
}

export interface CartResponse {
  id: string;
  items: CartItem[];
  item_count: number;
  subtotal: number;
  discount_amount: number;
  coupon_code: string | null;
  total: number;
  shipping_address: Address | null;
  billing_address: Address | null;
  billing_same_as_shipping: boolean;
}

// Stale JWT → clear auth and bounce to /login. Idempotent.
function handleUnauthorized() {
  if (typeof window === "undefined") return;
  localStorage.removeItem("ecommerce_user");
  localStorage.removeItem("ecommerce_access_token");
  localStorage.removeItem("ecommerce_refresh_token");
  if (!window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
}

class ApiClient {
  private token: string | null = null;
  private refreshToken: string | null = null;
  // Single in-flight refresh — rapid concurrent 401s share one network call.
  private inflightRefresh: Promise<string | null> | null = null;

  setToken(token: string | null) {
    this.token = token;
  }

  getToken() {
    return this.token;
  }

  setRefreshToken(token: string | null) {
    this.refreshToken = token;
  }

  /**
   * Attempt to swap the current refresh_token for a fresh access token.
   * Returns the new access token, or `null` if refresh isn't possible —
   * caller should then bounce the user to /login.
   */
  private async tryRefresh(): Promise<string | null> {
    if (!this.refreshToken) return null;
    if (this.inflightRefresh) return this.inflightRefresh;
    this.inflightRefresh = (async () => {
      try {
        const res = await fetch(`${API_URL}/api/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: this.refreshToken }),
        });
        if (!res.ok) return null;
        const data = (await res.json()) as { access_token?: string };
        if (!data.access_token) return null;
        this.token = data.access_token;
        if (typeof window !== "undefined") {
          localStorage.setItem("ecommerce_access_token", data.access_token);
        }
        return data.access_token;
      } catch {
        return null;
      } finally {
        this.inflightRefresh = null;
      }
    })();
    return this.inflightRefresh;
  }

  private async request<T>(
    path: string,
    options: RequestInit = {},
    { allowRefresh = true }: { allowRefresh?: boolean } = {}
  ): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    };
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }

    const res = await fetch(`${API_URL}${path}`, { ...options, headers });

    if (res.status === 401) {
      // One retry: if we have a refresh token, swap it for a fresh
      // access token and replay the request once. Avoids the silent
      // session-death audit finding on long chat sessions.
      if (allowRefresh) {
        const fresh = await this.tryRefresh();
        if (fresh) {
          return this.request<T>(path, options, { allowRefresh: false });
        }
      }
      this.token = null;
      handleUnauthorized();
      throw new Error("Session expired — please log in again.");
    }

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || body.error || `API error ${res.status}`);
    }

    return res.json();
  }

  // Auth
  signup(email: string, password: string, name: string) {
    return this.request<{
      access_token: string;
      refresh_token: string;
      user: { email: string; name: string; role: string };
    }>("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password, name }),
    });
  }

  login(email: string, password: string) {
    return this.request<{
      access_token: string;
      refresh_token: string;
      user: { email: string; name: string; role: string };
    }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  }

  refresh(refreshToken: string) {
    return this.request<{ access_token: string }>("/api/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  }

  // Chat
  chat(message: string, conversationId?: string, signal?: AbortSignal) {
    return this.request<{
      response: string;
      conversation_id: string;
      agents_involved: string[];
      message_id?: string;
    }>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, conversation_id: conversationId }),
      signal,
    });
  }

  /**
   * Streaming chat — reads SSE events and calls onChunk for each text delta.
   * Returns conversation metadata once the stream completes.
   *
   * Pass an `AbortSignal` to cancel mid-stream (e.g. user navigates away
   * or hits "Stop"). On a 401 the client refreshes once and retries the
   * stream; if the refresh fails the user is bounced to /login.
   */
  async chatStream(
    message: string,
    conversationId: string | undefined,
    onChunk: (text: string) => void,
    signal?: AbortSignal,
    options: { allowRefresh?: boolean } = {}
  ): Promise<{ conversation_id: string; agents_involved: string[] }> {
    const allowRefresh = options.allowRefresh ?? true;
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }

    const res = await fetch(`${API_URL}/api/chat/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify({ message, conversation_id: conversationId }),
      signal,
    });

    if (res.status === 401) {
      if (allowRefresh) {
        const fresh = await this.tryRefresh();
        if (fresh) {
          return this.chatStream(message, conversationId, onChunk, signal, {
            allowRefresh: false,
          });
        }
      }
      this.token = null;
      handleUnauthorized();
      throw new Error("Session expired — please log in again.");
    }

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || body.error || `API error ${res.status}`);
    }

    const reader = res.body?.getReader();
    if (!reader) {
      throw new Error("ReadableStream not supported");
    }

    const decoder = new TextDecoder();
    let metadata: { conversation_id: string; agents_involved: string[] } | null = null;
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        // Keep the last potentially incomplete line in the buffer
        buffer = lines.pop() ?? "";

        let currentEventType = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEventType = line.slice(7).trim();
            continue;
          }
          if (line.startsWith("data: ")) {
            const data = line.slice(6);

            if (data === "[DONE]") {
              continue;
            }

            if (currentEventType === "metadata") {
              try {
                metadata = JSON.parse(data);
              } catch {
                // Ignore malformed metadata
              }
              currentEventType = "";
              continue;
            }

            // Regular text chunk
            onChunk(data);
            currentEventType = "";
          }
        }
      }
    } catch (err) {
      // AbortError on user-initiated cancel: don't throw, just stop.
      if (err instanceof DOMException && err.name === "AbortError") {
        return metadata ?? { conversation_id: conversationId ?? "", agents_involved: [] };
      }
      throw err;
    } finally {
      reader.releaseLock();
    }

    return metadata ?? { conversation_id: conversationId ?? "", agents_involved: [] };
  }

  // Conversations
  getConversations() {
    return this.request<any[]>("/api/conversations");
  }

  getConversation(id: string) {
    return this.request<any>(`/api/conversations/${id}`);
  }

  deleteConversation(id: string) {
    return this.request<any>(`/api/conversations/${id}`, { method: "DELETE" });
  }

  // Marketplace
  getAgentCatalog() {
    return this.request<any[]>("/api/marketplace/agents");
  }

  requestAccess(agentName: string, roleRequested: string, useCase: string) {
    return this.request<any>("/api/marketplace/request", {
      method: "POST",
      body: JSON.stringify({
        agent_name: agentName,
        role_requested: roleRequested,
        use_case: useCase,
      }),
    });
  }

  getMyAgents() {
    return this.request<any[]>("/api/marketplace/my-agents");
  }

  // Admin
  getAccessRequests() {
    return this.request<any[]>("/api/admin/requests");
  }

  approveRequest(id: string, notes?: string) {
    return this.request<any>(`/api/admin/requests/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ admin_notes: notes }),
    });
  }

  denyRequest(id: string, notes?: string) {
    return this.request<any>(`/api/admin/requests/${id}/deny`, {
      method: "POST",
      body: JSON.stringify({ admin_notes: notes }),
    });
  }

  getUsageStats() {
    return this.request<any>("/api/admin/usage");
  }

  getAuditLog() {
    return this.request<any[]>("/api/admin/audit");
  }

  // Seller
  getSellerProducts() {
    return this.request<{ products: any[]; total: number }>("/api/seller/products");
  }

  getSellerOrders() {
    return this.request<{ orders: any[]; total: number }>("/api/seller/orders");
  }

  getSellerStats() {
    return this.request<any>("/api/seller/stats");
  }

  // Products
  getProducts(params?: { category?: string; min_price?: number; max_price?: number; search?: string; sort?: string }) {
    const qs = new URLSearchParams();
    if (params?.category) qs.set("category", params.category);
    if (params?.min_price !== undefined) qs.set("min_price", String(params.min_price));
    if (params?.max_price !== undefined) qs.set("max_price", String(params.max_price));
    if (params?.search) qs.set("search", params.search);
    if (params?.sort) qs.set("sort", params.sort);
    const q = qs.toString();
    return this.request<{ products: any[]; total: number; categories: string[] }>(`/api/products${q ? `?${q}` : ""}`);
  }

  getProduct(id: string) {
    return this.request<any>(`/api/products/${id}`);
  }

  // Cart
  getCart() {
    return this.request<{
      id: string;
      items: CartItem[];
      item_count: number;
      subtotal: number;
      discount_amount: number;
      coupon_code: string | null;
      total: number;
      shipping_address: Address | null;
      billing_address: Address | null;
      billing_same_as_shipping: boolean;
    }>("/api/cart");
  }

  addToCart(productId: string, quantity: number = 1) {
    return this.request<{ status: string; product_id: string; quantity: number }>(
      "/api/cart/items",
      {
        method: "POST",
        body: JSON.stringify({ product_id: productId, quantity }),
      }
    );
  }

  updateCartItem(itemId: string, quantity: number) {
    return this.request<{ status: string }>(`/api/cart/items/${itemId}`, {
      method: "PUT",
      body: JSON.stringify({ quantity }),
    });
  }

  removeCartItem(itemId: string) {
    return this.request<{ status: string }>(`/api/cart/items/${itemId}`, {
      method: "DELETE",
    });
  }

  applyCoupon(code: string) {
    return this.request<{
      status: string;
      code: string;
      discount_amount: number;
      description: string;
    }>("/api/cart/coupon", {
      method: "POST",
      body: JSON.stringify({ code }),
    });
  }

  removeCoupon() {
    return this.request<{ status: string }>("/api/cart/coupon", {
      method: "DELETE",
    });
  }

  updateCartAddress(data: {
    shipping_address?: Address;
    billing_address?: Address;
    billing_same_as_shipping?: boolean;
  }) {
    return this.request<{ status: string }>("/api/cart/address", {
      method: "PUT",
      body: JSON.stringify(data),
    });
  }

  checkout(data: {
    shipping_address: Address;
    billing_address?: Address | null;
    billing_same_as_shipping?: boolean;
    payment_method?: string;
  }) {
    return this.request<{
      order_id: string;
      total: number;
      item_count: number;
      status: string;
      tracking_number: string;
      carrier: string;
    }>("/api/checkout", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  cancelOrder(orderId: string, reason: string) {
    return this.request<{
      order_id: string;
      status: string;
      refund_amount: number;
    }>(`/api/orders/${orderId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
  }

  initiateReturn(orderId: string, reason: string, refundMethod: string) {
    return this.request<{
      return_id: string;
      order_id: string;
      status: string;
      return_label_url: string;
      refund_amount: number;
      refund_method: string;
    }>(`/api/orders/${orderId}/return`, {
      method: "POST",
      body: JSON.stringify({ reason, refund_method: refundMethod }),
    });
  }

  // Orders
  getOrders(status?: string) {
    const q = status ? `?status=${status}` : "";
    return this.request<{ orders: any[]; total: number }>(`/api/orders${q}`);
  }

  getOrder(id: string) {
    return this.request<any>(`/api/orders/${id}`);
  }

  // Profile
  getProfile() {
    return this.request<any>("/api/profile");
  }
}

export const api = new ApiClient();

// Named function exports for convenience (delegates to the singleton)
export function getConversations() {
  return api.getConversations();
}

export function getConversation(id: string) {
  return api.getConversation(id);
}

export function deleteConversation(id: string) {
  return api.deleteConversation(id);
}

export function chat(message: string, conversationId?: string) {
  return api.chat(message, conversationId);
}

export function chatStream(
  message: string,
  conversationId: string | undefined,
  onChunk: (text: string) => void,
) {
  return api.chatStream(message, conversationId, onChunk);
}
