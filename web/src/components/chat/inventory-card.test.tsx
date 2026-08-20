import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatInventoryCard } from "./inventory-card";

describe("ChatInventoryCard", () => {
  it("renders with only check_stock's fields", () => {
    render(
      <ChatInventoryCard
        data={{
          product_name: "Sony WH-1000XM5",
          in_stock: true,
          total_quantity: 42,
          warehouses: [
            { warehouse: "East DC", region: "east", quantity: 30, low_stock: false },
            { warehouse: "West DC", region: "west", quantity: 12, low_stock: true },
          ],
        }}
      />
    );
    expect(screen.getByText("Sony WH-1000XM5")).toBeInTheDocument();
    expect(screen.getByText("In Stock")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("East DC")).toBeInTheDocument();
    expect(screen.getByText(/12 \(low\)/)).toBeInTheDocument();
  });

  it("renders Out of Stock in a destructive tone", () => {
    render(<ChatInventoryCard data={{ product_name: "Widget", in_stock: false, total_quantity: 0 }} />);
    expect(screen.getByText("Out of Stock").className).toContain("text-destructive");
  });

  it("renders upcoming restocks when get_restock_schedule was also called", () => {
    render(
      <ChatInventoryCard
        data={{
          product_name: "Widget",
          upcoming_restocks: [{ warehouse: "East DC", expected_quantity: 100, expected_date: "2026-09-01" }],
        }}
      />
    );
    expect(screen.getByText("Upcoming restocks")).toBeInTheDocument();
    expect(screen.getByText("2026-09-01")).toBeInTheDocument();
  });

  it("renders sparsely when only a product name is given", () => {
    render(<ChatInventoryCard data={{ product_name: "Widget" }} />);
    expect(screen.getByText("Widget")).toBeInTheDocument();
  });

  describe("shipping options (Phase 8.4 Stage 5 — interactive)", () => {
    const shippingData = {
      product_name: "Sony WH-1000XM5",
      ships_from: { warehouse: "East DC", region: "east", quantity_available: 30 },
      shipping_options: [
        { carrier: "Standard Shipping", speed_tier: "standard", price: 5.99, delivery_window: "5-7 business days" },
        { carrier: "Overnight Shipping", speed_tier: "overnight", price: 24.99, delivery_window: "1 business day" },
      ],
    };

    it("renders each option with carrier, price, and delivery window", () => {
      render(<ChatInventoryCard data={shippingData} onAction={() => {}} />);
      expect(screen.getByText(/ships from East DC/)).toBeInTheDocument();
      expect(screen.getByText("Standard Shipping", { exact: false })).toBeInTheDocument();
      expect(screen.getByText("$5.99")).toBeInTheDocument();
      expect(screen.getByText("5-7 business days")).toBeInTheDocument();
      expect(screen.getByText("$24.99")).toBeInTheDocument();
    });

    it("calls onAction with a natural-language confirmation when Select is clicked", async () => {
      const onAction = vi.fn();
      render(<ChatInventoryCard data={shippingData} onAction={onAction} />);
      const selectButtons = screen.getAllByRole("button", { name: "Select" });
      await userEvent.click(selectButtons[1]);
      expect(onAction).toHaveBeenCalledWith(
        "I'll go with Overnight Shipping (overnight) shipping for $24.99, 1 business day."
      );
    });

    it("omits Select buttons when no onAction is provided (read-only render)", () => {
      render(<ChatInventoryCard data={shippingData} />);
      expect(screen.queryByRole("button", { name: "Select" })).not.toBeInTheDocument();
    });
  });
});
