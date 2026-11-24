import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import RunsPage from "./page";
import { api, type RunEntry } from "@/lib/api";

// Only staff may approve (#92), so the buttons are role-gated; each test sets the role.
const auth = vi.hoisted(() => ({ isStaff: true, email: "reviewer@example.com" }));
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ isStaff: auth.isStaff, user: { email: auth.email, name: "U", role: "x" } }),
}));

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

beforeEach(() => {
  auth.isStaff = true;
  auth.email = "reviewer@example.com";
});

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

  it("shows a customer 'Awaiting staff review' instead of Approve/Reject", async () => {
    auth.isStaff = false;
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
    const resume = vi.spyOn(api, "resumeRun");

    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("return order abc")).toBeInTheDocument());
    fireEvent.click(screen.getByText("return order abc"));

    await waitFor(() => expect(screen.getByText("Awaiting staff review")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    expect(resume).not.toHaveBeenCalled();
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

  it("shows a return waiting for the carrier as a wait, with nothing to approve", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ entries: [PENDING_RUN], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "getRunCheckpoints").mockResolvedValue({
      run_id: "run-1",
      checkpoints: [],
      hitl_request: {
        id: "hitl-2",
        kind: "carrier_scan",
        status: "pending",
        payload: { order_id: "order-abc", return_id: "ret-1" },
        response: null,
        created_at: new Date().toISOString(),
        responded_at: null,
      },
    });

    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("return order abc")).toBeInTheDocument());
    fireEvent.click(screen.getByText("return order abc"));

    await waitFor(() => expect(screen.getByText(/Carrier scan — pending/)).toBeInTheDocument());
    expect(screen.getByText(/Waiting for the carrier to scan the package/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    expect(screen.queryByText("Awaiting staff review")).not.toBeInTheDocument();
  });

  it("shows a return waiting for inspection, with the timeline and no buttons", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ entries: [PENDING_RUN], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "getRunCheckpoints").mockResolvedValue({
      run_id: "run-1",
      checkpoints: [],
      hitl_request: {
        id: "hitl-3",
        kind: "return_inspection",
        status: "pending",
        payload: { order_id: "order-abc", return_id: "ret-1" },
        response: null,
        created_at: new Date().toISOString(),
        responded_at: null,
      },
    });
    const timeline = vi.spyOn(api, "getRunTimeline").mockResolvedValue({
      run_id: "run-1",
      entries: [
        { at: "2026-11-15T10:00:00Z", kind: "run", title: "Return requested" },
        { at: "2026-11-15T10:01:00Z", kind: "carrier_scan", title: "The carrier scanned the package" },
        { at: "2026-11-15T10:02:00Z", kind: "checkpoint", title: "State saved", detail: "return-and-replace" },
        { at: "2026-11-15T10:03:00Z", kind: "return_inspection", title: "Waiting for inspection" },
      ],
    });

    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("return order abc")).toBeInTheDocument());
    fireEvent.click(screen.getByText("return order abc"));

    await waitFor(() => expect(screen.getByText(/Inspection — pending/)).toBeInTheDocument());
    expect(screen.getByText(/Waiting for support to inspect it/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    const list = await screen.findByRole("list", { name: "Return timeline" });
    expect(list).toHaveTextContent("Return requested");
    expect(list).toHaveTextContent("The carrier scanned the package");
    expect(list).not.toHaveTextContent("State saved"); // checkpoints are noise to a reader
    expect(timeline).toHaveBeenCalledWith("run-1");
  });

  it("does not fetch a timeline for a plain approval", async () => {
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
    const timeline = vi.spyOn(api, "getRunTimeline");

    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("return order abc")).toBeInTheDocument());
    fireEvent.click(screen.getByText("return order abc"));
    await waitFor(() => expect(screen.getByText(/Return approval — pending/)).toBeInTheDocument());

    expect(timeline).not.toHaveBeenCalled();
  });

  it("shows a timeline failure inline", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ entries: [PENDING_RUN], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "getRunCheckpoints").mockResolvedValue({
      run_id: "run-1",
      checkpoints: [],
      hitl_request: {
        id: "hitl-2",
        kind: "carrier_scan",
        status: "pending",
        payload: {},
        response: null,
        created_at: new Date().toISOString(),
        responded_at: null,
      },
    });
    vi.spyOn(api, "getRunTimeline").mockRejectedValue(new Error("Run not found"));

    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("return order abc")).toBeInTheDocument());
    fireEvent.click(screen.getByText("return order abc"));

    await waitFor(() => expect(screen.getByText("Run not found")).toBeInTheDocument());
  });

  it("shows a resolved carrier scan as resolved", async () => {
    vi.spyOn(api, "getRuns").mockResolvedValue({ entries: [PENDING_RUN], total: 1, limit: 20, offset: 0 });
    vi.spyOn(api, "getRunCheckpoints").mockResolvedValue({
      run_id: "run-1",
      checkpoints: [],
      hitl_request: {
        id: "hitl-2",
        kind: "carrier_scan",
        status: "completed",
        payload: { order_id: "order-abc" },
        response: { received: true },
        created_at: new Date().toISOString(),
        responded_at: new Date().toISOString(),
      },
    });

    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("return order abc")).toBeInTheDocument());
    fireEvent.click(screen.getByText("return order abc"));

    await waitFor(() => expect(screen.getByText(/Carrier scan — completed/)).toBeInTheDocument());
    expect(screen.getByText(/Resolved/)).toBeInTheDocument();
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

describe("RunsPage — who may decide", () => {
  async function renderPending(): Promise<ReturnType<typeof vi.spyOn>> {
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
    const resume = vi.spyOn(api, "resumeRun");
    render(<RunsPage />);
    await waitFor(() => expect(screen.getByText("return order abc")).toBeInTheDocument());
    fireEvent.click(screen.getByText("return order abc"));
    return resume;
  }

  it("lets support (staff, not admin) approve another customer's return", async () => {
    await renderPending();
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument());
    expect(screen.queryByText("Awaiting staff review")).not.toBeInTheDocument();
  });

  it("does not offer staff Approve on their own request", async () => {
    auth.email = "alice@example.com";
    await renderPending();
    await waitFor(() => expect(screen.getByText("Awaiting staff review")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
});
