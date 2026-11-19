import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import ReturnInspectionsPage from "./page";
import { api, type ReturnInspection } from "@/lib/api";

// A stable object: the page re-runs its load effect whenever `user` changes identity, as the real context never does.
const auth = vi.hoisted(() => ({
  value: { user: { email: "support@example.com" }, isLoading: false, isStaff: true } as {
    user: { email: string } | null;
    isLoading: boolean;
    isStaff: boolean;
  },
}));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => auth.value }));

function item(over: Partial<ReturnInspection> = {}): ReturnInspection {
  return {
    return_id: "aaaaaaaa-1111-2222-3333-444444444444",
    order_id: "bbbbbbbb-1111-2222-3333-444444444444",
    customer_email: "alice@example.com",
    customer_name: "Alice",
    reason: "arrived broken",
    refund_amount: 120,
    scanned_at: "2026-11-15T10:00:00Z",
    waiting_since: new Date().toISOString(),
    overdue_after_days: 7,
    ...over,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  auth.value = { user: { email: "support@example.com" }, isLoading: false, isStaff: true };
});

describe("ReturnInspectionsPage", () => {
  it("lists the returns waiting for inspection", async () => {
    vi.spyOn(api, "getReturnInspections").mockResolvedValue({ inspections: [item()], total: 1 });

    render(<ReturnInspectionsPage />);

    await waitFor(() => expect(screen.getByTestId("inspection-card")).toBeInTheDocument());
    expect(screen.getByText(/Alice/)).toBeInTheDocument();
    expect(screen.getByText(/arrived broken/)).toBeInTheDocument();
    expect(screen.getByText(/Waiting 0 days/)).toBeInTheDocument();
  });

  it("says when there is nothing to inspect", async () => {
    vi.spyOn(api, "getReturnInspections").mockResolvedValue({ inspections: [], total: 0 });

    render(<ReturnInspectionsPage />);

    await waitFor(() => expect(screen.getByText("Nothing to inspect")).toBeInTheDocument());
  });

  it("flags a return that has waited past its limit", async () => {
    const old = new Date(Date.now() - 9 * 86_400_000).toISOString();
    vi.spyOn(api, "getReturnInspections").mockResolvedValue({ inspections: [item({ waiting_since: old })], total: 1 });

    render(<ReturnInspectionsPage />);

    await waitFor(() => expect(screen.getByText(/Overdue: waiting 9 days/)).toBeInTheDocument());
  });

  it("accepts with the chosen resolution and a lower amount, then reloads the list", async () => {
    const list = vi
      .spyOn(api, "getReturnInspections")
      .mockResolvedValueOnce({ inspections: [item()], total: 1 })
      .mockResolvedValueOnce({ inspections: [], total: 0 });
    const inspect = vi
      .spyOn(api, "inspectReturn")
      .mockResolvedValue({ return_id: "r", status: "refunded", resolution: "store_credit", paid: 80.5 });

    render(<ReturnInspectionsPage />);
    await waitFor(() => expect(screen.getByTestId("inspection-card")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Resolution"), { target: { value: "store_credit" } });
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "80.5" } });
    fireEvent.change(screen.getByLabelText("Condition"), { target: { value: "like new" } });
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));

    await waitFor(() =>
      expect(inspect).toHaveBeenCalledWith("aaaaaaaa-1111-2222-3333-444444444444", {
        outcome: "accept",
        resolution: "store_credit",
        amount: 80.5,
        condition: "like new",
        note: undefined,
      }),
    );
    await waitFor(() => expect(screen.getByText(/Accepted: \$80.50 as store credit/)).toBeInTheDocument());
    expect(list).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.getByText("Nothing to inspect")).toBeInTheDocument());
  });

  it("rejects without sending a resolution or an amount", async () => {
    vi.spyOn(api, "getReturnInspections").mockResolvedValue({ inspections: [item()], total: 1 });
    const inspect = vi
      .spyOn(api, "inspectReturn")
      .mockResolvedValue({ return_id: "r", status: "denied", resolution: null, paid: 0 });

    render(<ReturnInspectionsPage />);
    await waitFor(() => expect(screen.getByTestId("inspection-card")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "50" } }); // ignored on a rejection
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() =>
      expect(inspect).toHaveBeenCalledWith("aaaaaaaa-1111-2222-3333-444444444444", {
        outcome: "reject",
        condition: undefined,
        note: undefined,
      }),
    );
    await waitFor(() => expect(screen.getByText(/Rejected: the customer is told/)).toBeInTheDocument());
  });

  it("refuses an amount above the original before asking the server", async () => {
    vi.spyOn(api, "getReturnInspections").mockResolvedValue({ inspections: [item()], total: 1 });
    const inspect = vi.spyOn(api, "inspectReturn");

    render(<ReturnInspectionsPage />);
    await waitFor(() => expect(screen.getByTestId("inspection-card")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "120.01" } });
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("cannot be higher than the original $120.00");
    expect(inspect).not.toHaveBeenCalled();
  });

  it("refuses a nonsense amount", async () => {
    vi.spyOn(api, "getReturnInspections").mockResolvedValue({ inspections: [item()], total: 1 });
    const inspect = vi.spyOn(api, "inspectReturn");

    render(<ReturnInspectionsPage />);
    await waitFor(() => expect(screen.getByTestId("inspection-card")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter an amount above zero");
    expect(inspect).not.toHaveBeenCalled();
  });

  it("shows the server's reason when the decision is refused", async () => {
    vi.spyOn(api, "getReturnInspections").mockResolvedValue({ inspections: [item()], total: 1 });
    vi.spyOn(api, "inspectReturn").mockRejectedValue(new Error("You cannot inspect your own return"));

    render(<ReturnInspectionsPage />);
    await waitFor(() => expect(screen.getByTestId("inspection-card")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("You cannot inspect your own return");
    expect(screen.getByTestId("inspection-card")).toBeInTheDocument(); // still there to decide again
  });

  it("is staff only", () => {
    auth.value = { user: { email: "alice@example.com" }, isLoading: false, isStaff: false };
    const list = vi.spyOn(api, "getReturnInspections");

    render(<ReturnInspectionsPage />);

    expect(screen.getByText("Staff only")).toBeInTheDocument();
    expect(list).not.toHaveBeenCalled();
  });

  it("shows a load failure", async () => {
    vi.spyOn(api, "getReturnInspections").mockRejectedValue(new Error("Staff access required"));

    render(<ReturnInspectionsPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Staff access required");
  });
});
