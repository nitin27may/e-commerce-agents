import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import RunsPage from "./page";
import { api, type RunEntry } from "@/lib/api";

const PENDING_RUN: RunEntry = {
  id: "run-1",
  agent_name: "orchestrator",
  user_email: "alice@example.com",
  user_name: "Alice",
  input_summary: "return order abc",
  tokens_in: 0,
  tokens_out: 0,
  tool_calls_count: 0,
  duration_ms: 300,
  status: "success",
  trace_id: null,
  created_at: new Date().toISOString(),
  steps: [], // workflow modes never produce agent_execution_steps rows
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RunsPage — checkpoint resume", () => {
  it("shows the 'Needs approval' badge in the collapsed list, without expanding first", async () => {
    // Regression test: checkpoint/HITL data used to be fetched lazily on
    // expand, so the badge — meant to be scannable across the whole list
    // — could never actually show until a row had already been opened
    // once. Fixed by fetching for every visible row up front.
    vi.spyOn(api, "getRuns").mockResolvedValue({ entries: [PENDING_RUN], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "getRunCheckpoints").mockResolvedValue({
      run_id: "run-1",
      checkpoints: [],
      hitl_request: {
        id: "hitl-1",
        status: "pending",
        payload: { order_id: "order-abc" },
        response: null,
        created_at: new Date().toISOString(),
        responded_at: null,
      },
    });

    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("Needs approval")).toBeInTheDocument());
    // Still collapsed — no Approve button visible yet.
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("shows a 'Needs approval' badge and lets the row expand even with no steps", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ entries: [PENDING_RUN], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "getRunCheckpoints").mockResolvedValue({
      run_id: "run-1",
      checkpoints: [{ checkpoint_id: "cp-1", workflow_name: "return-and-replace", created_at: new Date().toISOString() }],
      hitl_request: {
        id: "hitl-1",
        status: "pending",
        payload: { order_id: "order-abc", order_total: 720 },
        response: null,
        created_at: new Date().toISOString(),
        responded_at: null,
      },
    });

    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("return order abc")).toBeInTheDocument());

    fireEvent.click(screen.getByText("return order abc"));
    await waitFor(() => expect(screen.getByText(/Return approval — pending/)).toBeInTheDocument());
    expect(screen.getByText(/order order-abc/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
  });

  it("approving calls resumeRun and refreshes to show the resolved state", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ entries: [PENDING_RUN], total: 1, limit: 20, offset: 0 });
    const pending = {
      run_id: "run-1",
      checkpoints: [],
      hitl_request: {
        id: "hitl-1",
        status: "pending" as const,
        payload: { order_id: "order-abc" },
        response: null,
        created_at: new Date().toISOString(),
        responded_at: null,
      },
    };
    const resolved = {
      ...pending,
      hitl_request: { ...pending.hitl_request, status: "approved" as const, responded_at: new Date().toISOString() },
    };
    const getCheckpoints = vi.spyOn(api, "getRunCheckpoints").mockResolvedValueOnce(pending).mockResolvedValueOnce(resolved);
    const resume = vi.spyOn(api, "resumeRun").mockResolvedValue({
      run_id: "run-1",
      approved: true,
      text: "Return for order order-abc approved and finalized.",
      agents_involved: ["check-eligibility", "finalize"],
    });

    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("return order abc")).toBeInTheDocument());
    fireEvent.click(screen.getByText("return order abc"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(resume).toHaveBeenCalledWith("run-1", true));
    await waitFor(() => expect(screen.getByText(/Return approval — approved/)).toBeInTheDocument());
    expect(getCheckpoints).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("shows a resume error inline without crashing", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ entries: [PENDING_RUN], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "getRunCheckpoints").mockResolvedValue({
      run_id: "run-1",
      checkpoints: [],
      hitl_request: {
        id: "hitl-1",
        status: "pending",
        payload: {},
        response: null,
        created_at: new Date().toISOString(),
        responded_at: null,
      },
    });
    vi.spyOn(api, "resumeRun").mockRejectedValue(new Error("No pending approval found for this run"));

    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("return order abc")).toBeInTheDocument());
    fireEvent.click(screen.getByText("return order abc"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(screen.getByText("No pending approval found for this run")).toBeInTheDocument());
  });
});
