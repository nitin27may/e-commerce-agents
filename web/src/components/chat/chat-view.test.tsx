import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { api } from "@/lib/api";
import { ChatView } from "./chat-view";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { email: "alice@example.com", name: "Alice", role: "customer" }, isStaff: false, isLoading: false }),
}));
vi.mock("@/lib/cart-context", () => ({ useCart: () => ({ cart: null, refresh: vi.fn() }) }));

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "getConversations").mockResolvedValue([]);
  vi.spyOn(api, "getOrchestrationModes").mockResolvedValue([
    { name: "tool", label: "Tool Router", description: "", default: true,
      capabilities: { streams: true, supports_hitl: false, supports_checkpoints: false, is_graph: false } },
    { name: "workflow:pre-purchase", label: "Pre-Purchase Research", description: "", default: false,
      capabilities: { streams: true, supports_hitl: false, supports_checkpoints: false, is_graph: true } },
  ]);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 404 })));
});

describe("ChatView", () => {
  it("storefront chat offers no mode switcher and no Compare, for any role", async () => {
    render(<ChatView />);
    await waitFor(() => expect(screen.getAllByRole("textbox").length).toBeGreaterThan(0));
    expect(screen.queryByLabelText("Orchestration mode")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Compare/ })).not.toBeInTheDocument();
  });

  it("the Lab adds the mode switcher and Compare", async () => {
    render(<ChatView devView />);
    expect(await screen.findByLabelText("Orchestration mode")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Compare/ })).toBeInTheDocument();
  });
});
