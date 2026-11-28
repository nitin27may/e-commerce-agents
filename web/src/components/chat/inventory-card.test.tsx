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
    expect(screen.getByText("In stock")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText(/units available across 2 warehouses/)).toBeInTheDocument();
    expect(screen.getByText("East DC")).toBeInTheDocument();
    expect(screen.getByText(/12 \(low\)/)).toBeInTheDocument();
  });

  it("renders out of stock in a destructive tone, without a zero tile", () => {
    render(<ChatInventoryCard data={{ product_name: "Widget", in_stock: false, total_quantity: 0 }} />);
    expect(screen.getByText("Out of stock").className).toContain("text-destructive");
    // The badge already says it; no 24px "0" tile and no second line repeating it.
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.queryByText("Out of stock in every warehouse")).not.toBeInTheDocument();
  });

  it("says out of stock in one line when there is no stock badge", () => {
    render(<ChatInventoryCard data={{ total_quantity: 0 }} />);
    expect(screen.getByText("Out of stock in every warehouse").className).toContain("text-destructive");
  });

  it("says out of stock for a zero-quantity warehouse instead of '0 (low)'", () => {
    render(
      <ChatInventoryCard
        data={{ warehouses: [{ warehouse: "Central Warehouse", region: "central", quantity: 0, low_stock: true }] }}
      />
    );
    expect(screen.queryByText(/0 \(low\)/)).not.toBeInTheDocument();
    expect(screen.getByText("Out of stock").className).toContain("text-destructive");
    // The region is already in the warehouse name, so it gets no column of its own.
    expect(screen.queryByText("Region")).not.toBeInTheDocument();
  });

  it("titles a card without a product name by what it shows", () => {
    const { unmount } = render(<ChatInventoryCard data={{ warehouses: [{ warehouse: "East", quantity: 3 }] }} />);
    expect(screen.getByText("Stock availability")).toBeInTheDocument();
    unmount();
    render(<ChatInventoryCard data={{ shipping_options: [{ carrier: "Express Shipping", price: 14.99 }] }} />);
    expect(screen.getByText("Shipping options")).toBeInTheDocument();
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
    expect(screen.getByText("Sep 1, 2026")).toBeInTheDocument();
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
      expect(screen.getByText(/ships from East DC/i)).toBeInTheDocument();
      expect(screen.getByText("Standard Shipping", { exact: false })).toBeInTheDocument();
      expect(screen.getByText("$5.99")).toBeInTheDocument();
      expect(screen.getByText("5-7 business days")).toBeInTheDocument();
      expect(screen.getByText("$24.99")).toBeInTheDocument();
    });

    it("calls onAction with a natural-language confirmation when Choose is clicked, and marks the choice", async () => {
      const onAction = vi.fn();
      render(<ChatInventoryCard data={shippingData} onAction={onAction} />);
      const chooseButtons = screen.getAllByRole("button", { name: "Choose" });
      await userEvent.click(chooseButtons[1]);
      expect(onAction).toHaveBeenCalledWith(
        "I'll go with Overnight Shipping (overnight) shipping for $24.99, 1 business day."
      );
      expect(screen.getByRole("button", { name: "Chosen" })).toHaveAttribute("aria-pressed", "true");
    });

    it("does not repeat the speed tier the carrier name already says", () => {
      render(<ChatInventoryCard data={shippingData} />);
      expect(screen.getByText("Standard Shipping")).toBeInTheDocument();
      expect(screen.queryByText(/· standard/)).not.toBeInTheDocument();
    });

    it("omits Choose buttons when no onAction is provided (read-only render)", () => {
      render(<ChatInventoryCard data={shippingData} />);
      expect(screen.queryByRole("button", { name: "Choose" })).not.toBeInTheDocument();
    });
  });
});
