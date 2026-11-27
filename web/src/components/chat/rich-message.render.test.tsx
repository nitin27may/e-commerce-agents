import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { CARD_REGISTRY } from "@/lib/chat-schemas";
import { CARD_COMPONENTS } from "./card-registry";
import { RichMessage } from "./rich-message";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => ({ isAuthenticated: true }) }));
vi.mock("@/lib/cart-context", () => ({ useCart: () => ({ cart: null, addItem: vi.fn() }) }));

describe("CARD_COMPONENTS", () => {
  it("has exactly one component per registered card kind", () => {
    expect(Object.keys(CARD_COMPONENTS).sort()).toEqual(Object.keys(CARD_REGISTRY).sort());
  });
});

describe("RichMessage", () => {
  it("shows a card skeleton, not raw JSON, while a card fence is still streaming", () => {
    render(<RichMessage content={'Checking stock:\n```inventory\n{"product_id": "p1", "in_st'} streaming />);
    expect(screen.getByText("Checking stock:")).toBeInTheDocument();
    expect(screen.getByTestId("card-skeleton")).toBeInTheDocument();
    expect(screen.queryByText(/product_id/)).not.toBeInTheDocument();
  });

  it("renders the card once the fence closes", () => {
    render(<RichMessage content={'Checking stock:\n```inventory\n{"product_id": "p1", "in_stock": true}\n```'} />);
    expect(screen.queryByTestId("card-skeleton")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Stock availability" })).toBeInTheDocument();
  });

  it("renders a two-product fence as one comparison card with price, rating and review rows", () => {
    const pair = [
      { id: "dc9e2baa-8182-58f8-9df1-97d049426ba1", name: "Sony WH-1000XM5", price: 299.99, original_price: 349.99, rating: 4.7, review_count: 15 },
      { id: "08a21e63-49ac-5ec4-974e-cd3fc1761620", name: "AirPods Max", price: 449.99, rating: 4.5, review_count: 11 },
    ];
    render(<RichMessage content={"```products\n" + JSON.stringify(pair) + "\n```"} />);
    expect(screen.getByRole("region", { name: "Side-by-side comparison" })).toBeInTheDocument();
    expect(screen.getByText("Lower price")).toBeInTheDocument();
    expect(screen.getByText("Higher rating")).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "Reviews" })).toBeInTheDocument();
    // Money is formatted one way everywhere: with grouping, never "$1399.99".
    expect(screen.getAllByText("$299.99").length).toBeGreaterThan(0);
    expect(screen.getByText("Save 14%")).toBeInTheDocument();
  });
});
