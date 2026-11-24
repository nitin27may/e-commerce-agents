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

describe("AutonomyDecisionsPage — paging", () => {
  it("shows how many of the total are listed and loads older decisions with the next offset", async () => {
    const spy = vi
      .spyOn(api, "getAutonomyDecisions")
      .mockResolvedValueOnce(page([decision(3), decision(2)], { total: 3 }))
      .mockResolvedValueOnce(page([decision(1, "awaiting_approval")], { total: 3 }));

    render(<AutonomyDecisionsPage />);
    await waitFor(() => expect(screen.getByText("Showing 2 of 3 decisions, newest first.")).toBeInTheDocument());
    expect(screen.queryByText(/order 00000001/)).not.toBeInTheDocument();
    expect(spy.mock.calls[0][1]).toMatchObject({ limit: 50 });

    fireEvent.click(screen.getByRole("button", { name: "Load older decisions" }));

    await waitFor(() => expect(screen.getByText(/order 00000001/)).toBeInTheDocument());
    expect(spy.mock.calls[1][1]).toMatchObject({ limit: 50, offset: 2 });
    expect(screen.getByText("Showing 3 of 3 decisions, newest first.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load older decisions" })).not.toBeInTheDocument();
  });

  it("does not list a row twice when new decisions shifted the pages", async () => {
    vi.spyOn(api, "getAutonomyDecisions")
      .mockResolvedValueOnce(page([decision(3), decision(2)], { total: 3 }))
      .mockResolvedValueOnce(page([decision(2), decision(1)], { total: 4 })); // a new row pushed #2 down a page

    render(<AutonomyDecisionsPage />);
    await waitFor(() => expect(screen.getByText(/order 00000003/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Load older decisions" }));

    await waitFor(() => expect(screen.getByText(/order 00000001/)).toBeInTheDocument());
    expect(screen.getAllByText(/order 00000002/)).toHaveLength(1);
  });

  it("pages within a filter, and a filter change starts again from the newest", async () => {
    const spy = vi.spyOn(api, "getAutonomyDecisions").mockImplementation((state, opts) => {
      if (state === "awaiting_approval") {
        return Promise.resolve(
          opts?.offset
            ? page([decision(8, "awaiting_approval")], { total: 2 })
            : page([decision(9, "awaiting_approval")], { total: 2 }),
        );
      }
      return Promise.resolve(page([decision(1)], { total: 1 }));
    });

    render(<AutonomyDecisionsPage />);
    await waitFor(() => expect(screen.getByText(/order 00000001/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Waiting for approval" }));
    await waitFor(() => expect(screen.getByText(/order 00000009/)).toBeInTheDocument());
    expect(screen.queryByText(/order 00000001/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Load older decisions" }));

    await waitFor(() => expect(screen.getByText(/order 00000008/)).toBeInTheDocument());
    const last = spy.mock.calls.at(-1)!;
    expect(last[0]).toBe("awaiting_approval");
    expect(last[1]).toMatchObject({ offset: 1 });
  });

  it("a refresh keeps the rows already paged in", async () => {
    const spy = vi
      .spyOn(api, "getAutonomyDecisions")
      .mockResolvedValueOnce(page([decision(2)], { total: 2 }))
      .mockResolvedValueOnce(page([decision(1)], { total: 2 }))
      .mockResolvedValueOnce(page([decision(2), decision(1)], { total: 2 }));

    render(<AutonomyDecisionsPage />);
    await waitFor(() => expect(screen.getByText(/order 00000002/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Load older decisions" }));
    await waitFor(() => expect(screen.getByText(/order 00000001/)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(3));
    expect(spy.mock.calls[2][1]).toMatchObject({ limit: 50 }); // at least the loaded count, never fewer than a page
    expect(screen.getByText(/order 00000001/)).toBeInTheDocument();
  });

  it("the client sends limit and offset to the API", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => new Response(JSON.stringify(page([])), { status: 200 }));

    await api.getAutonomyDecisions("awaiting_approval", { limit: 25, offset: 50 });
    await api.getAutonomyDecisions();

    expect(String(fetchSpy.mock.calls[0][0])).toContain("/api/admin/decisions?state=awaiting_approval&limit=25&offset=50");
    expect(String(fetchSpy.mock.calls[1][0])).toMatch(/\/api\/admin\/decisions$/);
  });
});

describe("AutonomyDecisionsPage - monthly budget use (#299)", () => {
  const budget = {
    budget_key: "goodwill_credit",
    day: "2026-11-16",
    used_count: 1,
    used_amount: "5.00",
    max_count: 20,
    max_amount: "200.00",
    per_action_max_amount: "25.00",
    per_customer_max_count: 1,
    per_customer_max_amount: "25.00",
    per_order_max_count: 1,
  };

  it("shows this month's use beside today's when the budget has a month cap", async () => {
    vi.spyOn(api, "getAutonomyDecisions").mockResolvedValue(
      page([decision(1)], {
        budgets: [
          { ...budget, month: "2026-11", month_used_count: 12, month_used_amount: "60.00", month_max_count: 300, month_max_amount: "3000.00" },
        ],
      }),
    );

    render(<AutonomyDecisionsPage />);

    const month = await screen.findByTestId("budget-month");
    expect(month.textContent).toContain("2026-11");
    expect(month.textContent).toContain("12 of 300 actions");
    expect(month.textContent).toContain("$60.00 of $3,000.00");
  });

  it("shows no month line for a budget without a month cap", async () => {
    vi.spyOn(api, "getAutonomyDecisions").mockResolvedValue(
      page([decision(1)], { budgets: [{ ...budget, month_max_count: null, month_max_amount: null }] }),
    );

    render(<AutonomyDecisionsPage />);

    await screen.findByTestId("budget-meter");
    expect(screen.queryByTestId("budget-month")).not.toBeInTheDocument();
  });
});
