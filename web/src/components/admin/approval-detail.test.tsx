import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ApprovalDetail } from "@/lib/api";
import { ApprovalDetailView } from "./approval-detail";

const detail = {
  id: "a1",
  recommendation: {
    route: "manager",
    reasons: ["Amount over 500 needs a manager"],
    evidence: { facts: { amount: 1068.43, days_since_delivery: 9, loyalty_tier: "gold", refund_to_spend_ratio: 0.2544 } },
  },
  source_detail: {
    tool_name: "initiate_return",
    tool_input: { order_id: "9dee7fee-d86b-5cca-a912-4987f224d562", reason: "Stopped working", refund_method: "original_payment" },
  },
  events: [],
  editable_fields: [],
} as unknown as ApprovalDetail;

describe("ApprovalDetailView", () => {
  it("leads with the routing reason, the request and the facts in plain words", () => {
    render(<ApprovalDetailView detail={detail} />);
    expect(screen.getByText("Amount over 500 needs a manager")).toBeInTheDocument();
    expect(screen.getByText("#9dee7fee")).toBeInTheDocument();
    expect(screen.getByText("Original payment method")).toBeInTheDocument();
    expect(screen.getByText("$1,068.43")).toBeInTheDocument();
    expect(screen.getByText("25%")).toBeInTheDocument();
    expect(screen.getByText("Gold")).toBeInTheDocument();
  });

  it("keeps the raw record, collapsed", () => {
    render(<ApprovalDetailView detail={detail} />);
    expect(screen.getByText("Raw record")).toBeInTheDocument();
  });
});
