import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
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
});
