const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

class ApiClient {
  private token: string | null = null;

  setToken(token: string | null) {
    this.token = token;
  }

  getToken() {
    return this.token;
  }

  private async request<T>(
    path: string,
    options: RequestInit = {}
  ): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    };
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }

    const res = await fetch(`${API_URL}${path}`, { ...options, headers });

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
  chat(message: string, conversationId?: string) {
    return this.request<{
      response: string;
      conversation_id: string;
      agents_involved: string[];
      message_id?: string;
    }>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, conversation_id: conversationId }),
    });
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
