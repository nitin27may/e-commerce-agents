import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import ApprovalsPage from "./page";
import { api, type ApprovalSummary } from "@/lib/api";

// The inbox is for staff (support and admin); each test sets who is signed in.
const auth = vi.hoisted(() => ({ isStaff: true }));
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { email: "support@example.com", name: "S", role: "support" }, isStaff: auth.isStaff, isLoading: false }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));

const PAUSED_RETURN: ApprovalSummary = {
  id: "appr-1",
  source: "workflow",
  kind: "return_approval",
  requester_email: "tour@example.com",
  amount: 649.99,
  route: "support",
  assigned_role: "support",
  status: "pending_review",
  due_at: null,
  escalation_level: 0,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

beforeEach(() => {
  auth.isStaff = true;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ApprovalsPage", () => {
  it("lists a paused workflow return from the unified inbox, not only tool approvals", async () => {
    // Regression (#208): the page read the tool-approval list, so a return paused at the
    // workflow's policy gate never appeared and the inbox said "No pending approvals".
    const list = vi.spyOn(api, "listApprovals").mockResolvedValue({ approvals: [PAUSED_RETURN], total: 1 });

    render(<ApprovalsPage />);

    await waitFor(() => expect(screen.getByText("tour@example.com")).toBeInTheDocument());
    expect(list).toHaveBeenCalledWith(undefined); // the default: what is waiting
    expect(screen.getByText("Workflow")).toBeInTheDocument();
    expect(screen.getByText("$649.99")).toBeInTheDocument();
    expect(screen.getByText("Routed to support")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("is open to a support reviewer, and decides through the unified endpoint", async () => {
    vi.spyOn(api, "listApprovals").mockResolvedValue({ approvals: [PAUSED_RETURN], total: 1 });
    const decide = vi
      .spyOn(api, "decideApproval")
      .mockResolvedValue({ approval: { ...PAUSED_RETURN, status: "executed" }, result: {} });

    render(<ApprovalsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));

    await waitFor(() => expect(decide).toHaveBeenCalledWith("appr-1", "approve"));
  });

  it("rejects through the same endpoint", async () => {
    vi.spyOn(api, "listApprovals").mockResolvedValue({ approvals: [PAUSED_RETURN], total: 1 });
    const decide = vi
      .spyOn(api, "decideApproval")
      .mockResolvedValue({ approval: { ...PAUSED_RETURN, status: "rejected" }, result: { status: "rejected" } });

    render(<ApprovalsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));

    await waitFor(() => expect(decide).toHaveBeenCalledWith("appr-1", "reject"));
  });

  it("offers no decision on an approval that is already closed", async () => {
    vi.spyOn(api, "listApprovals").mockResolvedValue({
      approvals: [{ ...PAUSED_RETURN, status: "executed" }],
      total: 1,
    });

    render(<ApprovalsPage />);

    await waitFor(() => expect(screen.getByText("Executed")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("shows the server's reason when the list cannot be loaded", async () => {
    vi.spyOn(api, "listApprovals").mockRejectedValue(new Error("Staff role required"));

    render(<ApprovalsPage />);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Staff role required"));
  });

  it("refuses a signed-in customer without calling the API", () => {
    auth.isStaff = false;
    const list = vi.spyOn(api, "listApprovals");

    render(<ApprovalsPage />);

    expect(screen.getByText("Access Denied")).toBeInTheDocument();
    expect(list).not.toHaveBeenCalled();
  });
});
