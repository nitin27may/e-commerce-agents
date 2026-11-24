import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DecisionCard, money, reasonText } from "./decision-card";
import type { AutonomyDecision } from "@/lib/api";

const decision = (over: Partial<AutonomyDecision> = {}): AutonomyDecision => ({
  id: "d1",
  created_at: "2026-11-16T10:00:00Z",
  event_type: "order.shipment_delayed",
  action: "goodwill_credit",
  budget_key: "goodwill_credit",
  customer_email: "alice@example.com",
  order_id: "12345678-aaaa-bbbb-cccc-1234567890ab",
  amount: "5.00",
  applied_amount: null,
  tier: "auto",
  state: "executed",
  reasons: ["A small credit for a delay of at least two days"],
  budget: { budget_key: "goodwill_credit", day: "2026-11-16", tripped: [], drew: { count: 1, amount: "5.00" } },
  input: { event: { event_id: "e1", delay_days: 3 }, rule: "auto-small-credit" },
  policy_version: "v1",
  approval_id: null,
  approval_status: null,
  approved_by: null,
  reversed_at: null,
  reversed_by: null,
  can_reverse: true,
  ...over,
});

describe("DecisionCard", () => {
  it("shows what was done, why and the budget it drew on", () => {
    render(<DecisionCard decision={decision()} onReverse={vi.fn()} />);
    expect(screen.getByText(/goodwill credit \$5\.00 for alice@example.com/)).toBeTruthy();
    expect(screen.getByText("Done by the system")).toBeTruthy();
    expect(screen.getByText("Acted alone")).toBeTruthy();
    expect(screen.getByText("A small credit for a delay of at least two days")).toBeTruthy();
    expect(screen.getByText(/Drew \$5\.00 from the goodwill credit budget for 2026-11-16/)).toBeTruthy();
  });

  it("reverses only after a confirmation, passing the note", async () => {
    const onReverse = vi.fn().mockResolvedValue(undefined);
    render(<DecisionCard decision={decision()} onReverse={onReverse} />);
    fireEvent.click(screen.getByRole("button", { name: "Reverse" }));
    expect(onReverse).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: " asked " } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm reversal" }));
    await waitFor(() => expect(onReverse).toHaveBeenCalledWith("d1", "asked"));
  });

  it("can cancel the confirmation and shows a failure", async () => {
    const onReverse = vi.fn().mockRejectedValue(new Error("Already reversed"));
    render(<DecisionCard decision={decision()} onReverse={onReverse} />);
    fireEvent.click(screen.getByRole("button", { name: "Reverse" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("button", { name: "Confirm reversal" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reverse" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm reversal" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Already reversed");
  });

  it("offers no reverse button where nothing can be reversed", () => {
    render(
      <DecisionCard
        decision={decision({
          can_reverse: false,
          state: "awaiting_approval",
          tier: "needs_approval",
          amount: "40.00",
          reasons: ["Outside what the system may do alone", "budget:global_daily_count"],
          budget: { budget_key: "goodwill_credit", day: "2026-11-16", tripped: ["global_daily_count"], drew: null },
        })}
        onReverse={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "Reverse" })).toBeNull();
    expect(screen.getByText("Waiting for approval")).toBeTruthy();
    expect(screen.getByText("Today's limit on automatic actions is used up")).toBeTruthy();
    expect(screen.getByText(/Drew nothing: a limit was reached/)).toBeTruthy();
  });

  it("names who reversed it", () => {
    render(
      <DecisionCard
        decision={decision({ state: "reversed", can_reverse: false, reversed_by: "boss@example.com", reversed_at: "2026-11-16T11:00:00Z" })}
        onReverse={vi.fn()}
      />,
    );
    expect(screen.getByText(/Reversed by boss@example.com/)).toBeTruthy();
  });
});

describe("DecisionCard for the newer events", () => {
  it("shows a price adjustment with the product and the prices", () => {
    render(
      <DecisionCard
        decision={decision({
          event_type: "product.price_dropped",
          action: "price_adjustment",
          input: {
            event: { event_id: "e", line: { product_name: "Widget", paid_unit_price: "100.00", new_price: "95.00" } },
            rule: "auto-small-adjustment",
          },
        })}
        onReverse={vi.fn()}
      />,
    );
    expect(screen.getByText(/price adjustment \$5\.00 for alice@example.com/)).toBeTruthy();
    expect(screen.getByText(/Widget · paid \$100\.00, now \$95\.00/)).toBeTruthy();
  });

  it("shows a notice without an amount, and says why nothing was sent", () => {
    render(
      <DecisionCard
        decision={decision({
          action: "back_in_stock_notice",
          amount: null,
          budget_key: null,
          can_reverse: false,
          reasons: ["customer_daily_notice_cap"],
        })}
        onReverse={vi.fn()}
      />,
    );
    expect(screen.getByText("back-in-stock notice for alice@example.com")).toBeTruthy();
    expect(screen.getByText("This customer already received the most notices allowed today")).toBeTruthy();
  });

  it("shows the drafted customer message and where it came from", () => {
    const { rerender } = render(
      <DecisionCard
        decision={decision({ draft: { body: "Sorry about order 1. We added $5.00.", source: "llm", model: "m", fallback_reason: null } })}
        onReverse={vi.fn()}
      />,
    );
    expect(screen.getByText("Sorry about order 1. We added $5.00.")).toBeTruthy();
    expect(screen.getByText(/Worded by the ops-responder/)).toBeTruthy();
    rerender(
      <DecisionCard
        decision={decision({ draft: { body: "Standard.", source: "template", model: null, fallback_reason: "rejected:amount_not_in_facts" } })}
        onReverse={vi.fn()}
      />,
    );
    expect(screen.getByText(/the draft was not used: rejected:amount_not_in_facts/)).toBeTruthy();
  });
});

describe("DecisionCard for order risk and damaged items (#299)", () => {
  const risk = (over: Partial<AutonomyDecision> = {}) =>
    decision({
      event_type: "order.risk_flagged",
      action: "order_risk_review",
      budget_key: null,
      tier: "needs_approval",
      state: "awaiting_approval",
      amount: "600.00",
      reasons: ["A high-risk order is held until a person reviews it"],
      budget: {},
      hold_active: true,
      can_reverse: true,
      input: {
        event: {
          event_id: "e",
          risk: {
            score: 70,
            order_total: "600.00",
            hold: true,
            signals: [
              { signal: "large-order", points: 20, actual: "600.00" },
              { signal: "new-account", points: 25, actual: "1" },
            ],
          },
        },
        rule: "risk-high-hold",
      },
      ...over,
    });

  it("shows the score, the signals and that the order is on hold, without calling the order total a payment", () => {
    render(<DecisionCard decision={risk()} onReverse={vi.fn()} />);
    expect(screen.getByText("order risk review for alice@example.com")).toBeTruthy();
    expect(screen.getByText("Waiting for review")).toBeTruthy();
    expect(screen.getByText(/Risk score 70 of 100 for an order of \$600\.00/)).toBeTruthy();
    expect(screen.getByText("large order (+20)")).toBeTruthy();
    expect(screen.getByText(/The order is on hold until a person decides/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Release hold" })).toBeTruthy();
  });

  it("releases the hold only after a confirmation", async () => {
    const onReverse = vi.fn().mockResolvedValue(undefined);
    render(<DecisionCard decision={risk()} onReverse={onReverse} />);
    fireEvent.click(screen.getByRole("button", { name: "Release hold" }));
    expect(onReverse).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm release" }));
    await waitFor(() => expect(onReverse).toHaveBeenCalledWith("d1", undefined));
  });

  it("says who released a hold while the review is still waiting", () => {
    render(
      <DecisionCard
        decision={risk({ hold_active: false, can_reverse: false, reversed_by: "boss@example.com", reversed_at: "2026-11-16T11:00:00Z" })}
        onReverse={vi.fn()}
      />,
    );
    expect(screen.getByText(/The hold was released by boss@example.com/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Release hold" })).toBeNull();
    expect(screen.getByText("Waiting for review")).toBeTruthy();
  });

  it("names a cleared review and a medium one that held nothing", () => {
    const { rerender } = render(
      <DecisionCard decision={risk({ state: "executed_after_approval", hold_active: false, can_reverse: false })} onReverse={vi.fn()} />,
    );
    expect(screen.getByText("Cleared by staff")).toBeTruthy();
    rerender(
      <DecisionCard
        decision={risk({ hold_active: false, can_reverse: false, reasons: ["risk_low"] })}
        onReverse={vi.fn()}
      />,
    );
    expect(screen.getByText("The order looks ordinary, so nothing was done")).toBeTruthy();
    expect(screen.getByText("The order was not held.")).toBeTruthy();
  });

  it("shows a damaged-item remedy with the quantity and what was offered", () => {
    render(
      <DecisionCard
        decision={decision({
          event_type: "order.item_damaged",
          action: "damage_goodwill",
          budget_key: "damage_goodwill",
          amount: "12.00",
          input: {
            event: {
              event_id: "e",
              resolution: "replacement_offer",
              line: { product_name: "Kettle", damaged_quantity: 2 },
            },
            rule: "notify-medium-damage",
          },
        })}
        onReverse={vi.fn()}
      />,
    );
    expect(screen.getByText(/damaged-item remedy \$12\.00 for alice@example.com/)).toBeTruthy();
    expect(screen.getByText(/Kettle · 2 damaged · free replacement offered/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reverse" })).toBeTruthy();
  });

  it("explains a tripped monthly cap", () => {
    render(
      <DecisionCard
        decision={decision({
          can_reverse: false,
          state: "awaiting_approval",
          tier: "needs_approval",
          reasons: ["budget:global_monthly_count"],
          budget: { budget_key: "goodwill_credit", day: "2026-11-16", tripped: ["global_monthly_count"], drew: null },
        })}
        onReverse={vi.fn()}
      />,
    );
    expect(screen.getByText("This month's limit on automatic actions is used up")).toBeTruthy();
  });
});

describe("helpers", () => {
  it("formats decimal strings and tolerates junk", () => {
    expect(money("12.5")).toBe("$12.50");
    expect([money(null), money(""), money("x")]).toEqual(["-", "-", "-"]);
  });
  it("falls back to the code for an unknown reason", () => {
    expect(reasonText("something_new")).toBe("something_new");
  });
});
