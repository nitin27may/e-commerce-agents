import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
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
    expect(screen.getByText("Your savings")).toBeInTheDocument();
    expect(screen.getByText("Coupon SAVE10")).toBeInTheDocument();
    expect(screen.getByText("Gold loyalty discount")).toBeInTheDocument();
    expect(screen.getByText("$304.48")).toBeInTheDocument();
    expect(screen.getByText(/you save 13%/)).toBeInTheDocument();
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
    expect(screen.getByText("Deals and promotions")).toBeInTheDocument();
    expect(screen.getByText("WELCOME15")).toBeInTheDocument();
    expect(screen.getByText("Summer Sale")).toBeInTheDocument();
    expect(screen.getByText("Flash sale")).toBeInTheDocument();
    expect(screen.getByText("Sep 1, 2026")).toBeInTheDocument();
    expect(screen.queryByText("Your savings")).not.toBeInTheDocument();
  });

  it("drops columns no row has a value for, reads a promotion's percentage from its rules, and lets details wrap", () => {
    render(
      <ChatPricingCard
        data={{
          coupons: [{ code: "TECHSAVE", description: "15% off Electronics (min $100, max $75)" }],
          promotions: [{ name: "Tech Bundle Deal", type: "bundle", rules: { discount_pct: 10 } }],
        }}
      />
    );
    // No coupon has a discount value or end date, and no promotion an end date.
    expect(screen.queryByText("—")).not.toBeInTheDocument();
    expect(screen.queryByText("Ends")).not.toBeInTheDocument();
    expect(screen.getByText("10%")).toBeInTheDocument();
    expect(screen.getByText("Bundle")).toBeInTheDocument();
    expect(screen.getByText(/15% off Electronics/).closest("td")?.className).toContain("whitespace-normal");
  });

  it("offers Apply per coupon, which asks the assistant to apply it", async () => {
    const onAction = vi.fn();
    render(<ChatPricingCard data={{ coupons: [{ code: "WELCOME10", description: "10% off" }] }} onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onAction).toHaveBeenCalledWith("Apply coupon WELCOME10 to my cart");
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
