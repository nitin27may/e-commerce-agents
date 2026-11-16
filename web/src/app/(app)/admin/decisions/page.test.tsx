import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import AutonomyDecisionsPage from "./page";
import { api, type AutonomyDecision, type AutonomyDecisionsResponse } from "@/lib/api";

// A stable object: the page re-runs its load effect whenever `user` changes identity, as the real context never does.
const auth = vi.hoisted(() => ({ value: { user: { email: "admin@example.com" }, isLoading: false, isAdmin: true } }));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => auth.value }));

function decision(n: number, state = "executed"): AutonomyDecision {
  return {
    id: `d-${n}`,
    created_at: "2026-11-16T10:00:00Z",
    event_type: "order.shipment_delayed",
    action: "goodwill_credit",
    budget_key: "goodwill_credit",
    customer_email: `customer${n}@example.com`,
    order_id: `${String(n).padStart(8, "0")}-aaaa-bbbb-cccc-1234567890ab`,
    amount: "5.00",
    applied_amount: null,
    tier: "auto",
    state,
    reasons: ["A small credit"],
    budget: { budget_key: "goodwill_credit", day: "2026-11-16", tripped: [], drew: null },
    input: { event: { event_id: `e${n}`, delay_days: 3 }, rule: "auto-small-credit" },
    policy_version: "v1",
    approval_id: null,
    approval_status: null,
    approved_by: null,
    reversed_at: null,
    reversed_by: null,
    can_reverse: false,
  } as AutonomyDecision;
}

function page(decisions: AutonomyDecision[], extra: Partial<AutonomyDecisionsResponse> = {}): AutonomyDecisionsResponse {
  return {
    decisions,
    total: decisions.length,
    budgets: [],
    enabled: true,
    simulator_enabled: true,
    scenarios: { small: 3 },
    ...extra,
  } as AutonomyDecisionsResponse;
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AutonomyDecisionsPage — out-of-order responses", () => {
  it("a slow earlier response cannot overwrite the current filter's results", async () => {
    const slowAll = deferred<AutonomyDecisionsResponse>();
    const spy = vi.spyOn(api, "getAutonomyDecisions").mockImplementation((state) => {
      if (!state) return slowAll.promise; // the "All" request never settles until the test says so
      return Promise.resolve(page([decision(2, "reversed")]));
    });

    render(<AutonomyDecisionsPage />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Reversed" }));
    await waitFor(() => expect(screen.getByText(/order 00000002/)).toBeInTheDocument());

    // the slow "All" answer arrives last, with different rows
    await act(async () => slowAll.resolve(page([decision(1), decision(3)])));

    expect(screen.getByText(/order 00000002/)).toBeInTheDocument();
    expect(screen.queryByText(/order 00000001/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reversed" })).toHaveAttribute("aria-pressed", "true");
  });

  it("aborts the request it supersedes", async () => {
    const signals: AbortSignal[] = [];
    vi.spyOn(api, "getAutonomyDecisions").mockImplementation((_state, opts) => {
      if (opts?.signal) signals.push(opts.signal);
      return new Promise(() => {});
    });

    render(<AutonomyDecisionsPage />);
    await waitFor(() => expect(signals).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Reversed" }));
    await waitFor(() => expect(signals).toHaveLength(2));

    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
  });

  it("an error from a superseded request is not shown", async () => {
    const slow = deferred<AutonomyDecisionsResponse>();
    vi.spyOn(api, "getAutonomyDecisions").mockImplementation((state) =>
      state ? Promise.resolve(page([decision(2, "reversed")])) : slow.promise,
    );

    render(<AutonomyDecisionsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Reversed" }));
    await waitFor(() => expect(screen.getByText(/order 00000002/)).toBeInTheDocument());
    await act(async () => {
      // reject after the fact, as an aborted fetch would
      slow.resolve(Promise.reject(new DOMException("aborted", "AbortError")) as never);
      await Promise.resolve();
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("AutonomyDecisionsPage — refresh", () => {
  it("has a Refresh button that reloads decisions and budgets", async () => {
    const spy = vi
      .spyOn(api, "getAutonomyDecisions")
      .mockResolvedValueOnce(page([decision(1)]))
      .mockResolvedValueOnce(page([decision(1), decision(2)]));

    render(<AutonomyDecisionsPage />);
    await waitFor(() => expect(screen.getByText(/order 00000001/)).toBeInTheDocument());
    expect(screen.queryByText(/order 00000002/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    await waitFor(() => expect(screen.getByText(/order 00000002/)).toBeInTheDocument());
    expect(spy).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
  });

  describe("after a simulated scenario", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    async function sendScenario() {
      vi.spyOn(api, "simulateAutonomyEvent").mockResolvedValue({
        enqueued: true,
        outbox_id: 1,
        event_id: "e",
        order_id: "00000007-aaaa-bbbb-cccc-1234567890ab",
        delay_days: 3,
      });
      render(<AutonomyDecisionsPage />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /small delay/ }));
        await vi.advanceTimersByTimeAsync(0);
      });
    }

    it("looks again until the new decision shows up, then stops by itself", async () => {
      vi.useFakeTimers();
      const spy = vi
        .spyOn(api, "getAutonomyDecisions")
        .mockResolvedValueOnce(page([decision(1)]))
        .mockResolvedValueOnce(page([decision(1)])) // the worker has not got to it yet
        .mockResolvedValue(page([decision(9), decision(1)]));

      await sendScenario();
      expect(spy).toHaveBeenCalledTimes(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(spy).toHaveBeenCalledTimes(2);
      expect(screen.queryByText(/order 00000009/)).not.toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(spy).toHaveBeenCalledTimes(3);
      expect(screen.getByText(/order 00000009/)).toBeInTheDocument();

      for (let i = 0; i < 10; i++) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(2000);
        });
      }
      expect(spy).toHaveBeenCalledTimes(3); // found it: no more polling
    });

    it("gives up after a bounded number of looks when nothing arrives", async () => {
      vi.useFakeTimers();
      const spy = vi.spyOn(api, "getAutonomyDecisions").mockResolvedValue(page([decision(1)]));

      await sendScenario();
      for (let i = 0; i < 10; i++) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(2000);
        });
      }

      expect(spy).toHaveBeenCalledTimes(1 + 5);
    });
  });
});
