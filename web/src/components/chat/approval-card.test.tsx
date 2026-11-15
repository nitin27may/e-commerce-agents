import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalCard } from "./approval-card";
import { api } from "@/lib/api";

// Only staff may approve (#92); the card is role-gated, so each test sets the role.
const auth = vi.hoisted(() => ({ isAdmin: true }));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => ({ isAdmin: auth.isAdmin }) }));

beforeEach(() => {
  auth.isAdmin = true;
});

afterEach(() => {
  vi.restoreAllMocks();
});

const RESUMED = {
  run_id: "run-1",
  approved: true,
  text: "Return approved. A replacement ships tomorrow.",
  agents_involved: ["order-management"],
};

describe("ApprovalCard", () => {
  it("shows a customer a read-only awaiting-staff-review state, with no decision buttons", () => {
    // The requester must not approve their own return; the backend 403s them anyway.
    auth.isAdmin = false;
    const resume = vi.spyOn(api, "resumeRun");
    render(<ApprovalCard runId="run-1" onResolved={() => {}} />);

    expect(screen.getByText(/awaiting staff review/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reject/i })).not.toBeInTheDocument();
    expect(resume).not.toHaveBeenCalled();
  });

  it("offers an admin both decisions while the run is paused", () => {
    render(<ApprovalCard runId="run-1" onResolved={() => {}} />);

    expect(screen.getByRole("button", { name: /approve/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /reject/i })).toBeEnabled();
  });

  it("resumes the run and hands the resumed turn back to the thread", async () => {
    const resume = vi.spyOn(api, "resumeRun").mockResolvedValue(RESUMED);
    const onResolved = vi.fn();
    const user = userEvent.setup();
    render(<ApprovalCard runId="run-1" onResolved={onResolved} />);

    await user.click(screen.getByRole("button", { name: /approve/i }));

    expect(resume).toHaveBeenCalledWith("run-1", true);
    await waitFor(() =>
      expect(onResolved).toHaveBeenCalledWith({
        approved: true,
        text: RESUMED.text,
        agentsInvolved: ["order-management"],
      })
    );
  });

  it("passes the rejection through as false, not as a skipped call", async () => {
    // Rejecting still resumes the workflow — it takes the other branch. Not
    // calling resume at all would leave the run paused forever.
    const resume = vi.spyOn(api, "resumeRun").mockResolvedValue({ ...RESUMED, approved: false });
    const user = userEvent.setup();
    render(<ApprovalCard runId="run-9" onResolved={() => {}} />);

    await user.click(screen.getByRole("button", { name: /reject/i }));

    expect(resume).toHaveBeenCalledWith("run-9", false);
  });

  it("replaces both buttons with the outcome once resolved", async () => {
    // A decision that leaves its buttons live invites a second approval on a
    // run that has already moved on.
    vi.spyOn(api, "resumeRun").mockResolvedValue(RESUMED);
    const user = userEvent.setup();
    render(<ApprovalCard runId="run-1" onResolved={() => {}} />);

    await user.click(screen.getByRole("button", { name: /approve/i }));

    await waitFor(() => expect(screen.getByText(/Approved/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /reject/i })).not.toBeInTheDocument();
  });

  it("disables both buttons while a decision is in flight", async () => {
    let release: (v: typeof RESUMED) => void = () => {};
    vi.spyOn(api, "resumeRun").mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      })
    );
    const user = userEvent.setup();
    render(<ApprovalCard runId="run-1" onResolved={() => {}} />);

    await user.click(screen.getByRole("button", { name: /approve/i }));

    expect(screen.getByRole("button", { name: /reject/i })).toBeDisabled();
    release(RESUMED);
  });

  it("shows the failure and stays actionable when resume fails", async () => {
    // Silently reverting to two live buttons would hide that the decision
    // never landed, and the run would sit paused with nothing to show for it.
    vi.spyOn(api, "resumeRun").mockRejectedValue(new Error("checkpoint not found"));
    const user = userEvent.setup();
    render(<ApprovalCard runId="run-1" onResolved={() => {}} />);

    await user.click(screen.getByRole("button", { name: /approve/i }));

    await waitFor(() => expect(screen.getByText(/checkpoint not found/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /approve/i })).toBeEnabled();
  });

  it("does not report a resolution the backend rejected", async () => {
    vi.spyOn(api, "resumeRun").mockRejectedValue(new Error("nope"));
    const onResolved = vi.fn();
    const user = userEvent.setup();
    render(<ApprovalCard runId="run-1" onResolved={onResolved} />);

    await user.click(screen.getByRole("button", { name: /approve/i }));

    await waitFor(() => expect(screen.getByText(/nope/)).toBeInTheDocument());
    expect(onResolved).not.toHaveBeenCalled();
  });
});
