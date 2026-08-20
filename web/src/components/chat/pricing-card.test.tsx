import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatPricingCard } from "./pricing-card";

describe("ChatPricingCard", () => {
  it("renders a discount waterfall from optimize_cart's fields", () => {
    render(
      <ChatPricingCard
        data={{
          original_total: 349.98,
          savings: [
            { type: "coupon", code: "SAVE10", amount: 35 },
            { type: "loyalty_discount", tier: "gold", amount: 10.5 },
          ],
          total_savings: 45.5,
          final_total: 304.48,
          savings_percentage: 13,
        }}
      />
    );
    expect(screen.getByText("Savings Breakdown")).toBeInTheDocument();
    expect(screen.getByText("Coupon SAVE10")).toBeInTheDocument();
    expect(screen.getByText("Gold loyalty discount")).toBeInTheDocument();
    expect(screen.getByText("$304.48")).toBeInTheDocument();
    expect(screen.getByText(/saved 13%/)).toBeInTheDocument();
  });

  it("renders active deals from get_active_deals's fields with no waterfall", () => {
    render(
      <ChatPricingCard
        data={{
          coupons: [{ code: "WELCOME15", description: "New customer discount", discount_type: "percentage", discount_value: 15 }],
          promotions: [{ name: "Summer Sale", type: "flash_sale", end_date: "2026-09-01" }],
        }}
      />
    );
    expect(screen.getByText("Deals & Promotions")).toBeInTheDocument();
    expect(screen.getByText("WELCOME15")).toBeInTheDocument();
    expect(screen.getByText("Summer Sale")).toBeInTheDocument();
    expect(screen.queryByText("Savings Breakdown")).not.toBeInTheDocument();
  });

  it("renders nothing when the fence is effectively empty (Phase 8.4 Stage 4c live-testing find)", () => {
    // A real bug found live: optimize_cart couldn't resolve a cart (no
    // items given), and the model still emitted a `pricing` fence with no
    // populated fields — rendering a header with a blank body underneath.
    const { container } = render(<ChatPricingCard data={{}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("labels bundle and flash-sale savings lines correctly", () => {
    render(
      <ChatPricingCard
        data={{
          original_total: 100,
          savings: [
            { type: "bundle_promotion", name: "Headphone + Case Bundle", amount: 20 },
            { type: "flash_sale", name: "Flash Friday", product: "Sony WH-1000XM5", amount: 15 },
          ],
        }}
      />
    );
    expect(screen.getByText("Headphone + Case Bundle")).toBeInTheDocument();
    expect(screen.getByText("Flash Friday (Sony WH-1000XM5)")).toBeInTheDocument();
  });
});
