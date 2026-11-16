import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProposalCard, describeChange, editBounds, reasonText, toNumber } from "./proposal-card";
import type { ProposalLine, SellerProposal } from "@/lib/api";

const line = (over: Partial<ProposalLine>): ProposalLine => ({
  id: "l1",
  product_id: "p1",
  product_name: "Clean Code",
  field: "price",
  current_value: 37.49,
  proposed_value: 33.75,
  final_value: null,
  rationale: "410 units on hand, 9 sold.",
  evidence: {},
  guard: { ok: true, reasons: [], numbers: {}, policy_version: "v" },
  status: "proposed",
  executed_at: null,
  ...over,
});

const proposal = (lines: ProposalLine[], status: SellerProposal["status"] = "pending_review"): SellerProposal => ({
  id: "pr1",
  seller_email: "s@example.com",
  kind: "mixed",
  trigger: "manual",
  status,
  created_at: "2026-11-16T10:00:00Z",
  updated_at: "2026-11-16T10:00:00Z",
  approval_id: "a1",
  approval_status: "pending_review",
  due_at: null,
  lines,
});

describe("describeChange", () => {
  it("shows a price move and a restock quantity", () => {
    expect(describeChange(line({}))).toBe("$37.49 → $33.75");
    expect(describeChange(line({ field: "restock_quantity", current_value: 4, proposed_value: 26 }))).toBe(
      "Order 26 units (4 on hand)",
    );
  });
  it("parses decimals that arrive as strings instead of passing them to the formatter", () => {
    const strings = line({ current_value: "37.49", proposed_value: "33.75" });
    expect(describeChange(strings)).toBe("$37.49 → $33.75");
    expect(describeChange(strings, "35.00")).toBe("$37.49 → $35.00");
    expect(describeChange(line({ field: "restock_quantity", current_value: "4", proposed_value: "26" }))).toBe(
      "Order 26 units (4 on hand)",
    );
    expect(describeChange(line({ current_value: null, proposed_value: "abc" }))).toBe("$0.00 → $0.00");
  });
  it("converts only finite decimals", () => {
    expect(toNumber("12.50")).toBe(12.5);
    expect(toNumber(3)).toBe(3);
    expect([toNumber(null), toNumber(undefined), toNumber(""), toNumber("x"), toNumber("Infinity")]).toEqual([
      null,
      null,
      null,
      null,
      null,
    ]);
  });
  it("bounds an edit the way the server does", () => {
    expect(editBounds(line({}))).toEqual({ min: 33.75, max: 37.49 });
    expect(editBounds(line({ field: "restock_quantity", current_value: 4, proposed_value: "26" }))).toEqual({
      min: 1,
      max: 26,
    });
  });
  it("turns reason codes into words", () => {
    expect(reasonText("price_moved")).toMatch(/changed/i);
    expect(reasonText("margin_below_floor")).toMatch(/margin/i);
    expect(reasonText("something_new")).toBe("something new");
  });
});

describe("ProposalCard", () => {
  it("accepts every actionable line by default and rejects a blocked one, which cannot be accepted", async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined);
    const blocked = line({
      id: "l2",
      product_name: "Thin",
      status: "blocked",
      guard: { ok: false, reasons: ["margin_below_floor"], numbers: {}, policy_version: "v" },
    });
    render(<ProposalCard proposal={proposal([line({}), blocked])} onDecide={onDecide} />);

    expect(screen.getByText(/Blocked: Margin would fall below the floor/)).toBeInTheDocument();
    const group = screen.getByRole("group", { name: "Decision for Thin" });
    expect(group.querySelectorAll("button:disabled")).toHaveLength(2); // accept and edit
    fireEvent.click(screen.getByRole("button", { name: "Apply decisions" }));

    await waitFor(() =>
      expect(onDecide).toHaveBeenCalledWith("pr1", [
        { line_id: "l1", decision: "accept" },
        { line_id: "l2", decision: "reject" },
      ]),
    );
  });

  it("sends an edited value and refuses an invalid one", async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined);
    render(<ProposalCard proposal={proposal([line({})])} onDecide={onDecide} />);

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const input = screen.getByLabelText("Edited price for Clean Code");
    fireEvent.change(input, { target: { value: "-1" } });
    expect(screen.getByRole("button", { name: "Apply decisions" })).toBeDisabled();
    fireEvent.change(input, { target: { value: "35.00" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply decisions" }));

    await waitFor(() => expect(onDecide).toHaveBeenCalledWith("pr1", [{ line_id: "l1", decision: "edit", value: "35.00" }]));
  });

  it("renders a proposal whose decimals are strings and refuses an edit outside the server's bounds", () => {
    const strings = line({ current_value: "37.49", proposed_value: "33.75" });
    render(<ProposalCard proposal={proposal([strings])} onDecide={vi.fn()} />);
    expect(screen.getByText("$37.49 → $33.75")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const input = screen.getByLabelText("Edited price for Clean Code");
    fireEvent.change(input, { target: { value: "30.00" } }); // deeper than proposed
    expect(screen.getByRole("button", { name: "Apply decisions" })).toBeDisabled();
    fireEvent.change(input, { target: { value: "40.00" } }); // above where it started
    expect(screen.getByRole("button", { name: "Apply decisions" })).toBeDisabled();
    fireEvent.change(input, { target: { value: "35.00" } });
    expect(screen.getByRole("button", { name: "Apply decisions" })).toBeEnabled();
  });

  it("shows the server's refusal", async () => {
    const onDecide = vi.fn().mockRejectedValue(new Error("Proposal is already executed"));
    render(<ProposalCard proposal={proposal([line({})])} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: "Apply decisions" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Proposal is already executed");
  });

  it("is read-only once decided and shows each line's outcome", () => {
    render(<ProposalCard proposal={proposal([line({ status: "executed", final_value: 33.75 })], "executed")} onDecide={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Apply decisions" })).not.toBeInTheDocument();
    expect(screen.getAllByText("executed")).toHaveLength(2); // the batch and the line
  });
});
