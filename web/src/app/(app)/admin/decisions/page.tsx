"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Gauge, Loader2, Play } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { api, type AutonomyBudget, type AutonomyDecision, type AutonomyDecisionsResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { DecisionCard, money } from "@/components/admin/decision-card";

const FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All" },
  { value: "executed", label: "Done by the system" },
  { value: "awaiting_approval", label: "Waiting for approval" },
  { value: "reversed", label: "Reversed" },
  { value: "no_action", label: "Nothing to do" },
];

function BudgetMeter({ budget }: { budget: AutonomyBudget }) {
  const pct = Math.min(100, Math.round((budget.used_count / Math.max(budget.max_count, 1)) * 100));
  return (
    <div className="rounded-lg border p-3 text-sm" data-testid="budget-meter">
      <p className="font-medium">{budget.budget_key.replaceAll("_", " ")} budget</p>
      <p className="text-muted-foreground">
        {budget.used_count} of {budget.max_count} actions and {money(budget.used_amount)} of {money(budget.max_amount)} used
        today ({budget.day}, UTC)
      </p>
      <div
        role="progressbar"
        aria-label={`${budget.budget_key} actions used today`}
        aria-valuemin={0}
        aria-valuemax={budget.max_count}
        aria-valuenow={budget.used_count}
        className="mt-2 h-2 w-full overflow-hidden rounded bg-muted"
      >
        <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        At most {money(budget.per_action_max_amount)} per action, {budget.per_customer_max_count} per customer per day (
        {money(budget.per_customer_max_amount)}), {budget.per_order_max_count} per order.
      </p>
    </div>
  );
}

export default function AutonomyDecisionsPage() {
  const { user, isLoading: authLoading, isAdmin } = useAuth();
  const [data, setData] = useState<AutonomyDecisionsResponse | null>(null);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [simulating, setSimulating] = useState<string | null>(null);

  // Responses can finish out of order (a slow "All" after a quick "Reversed"). Only the newest request may write
  // data, error or loading, and starting one aborts the previous so the browser also stops waiting for it.
  const latest = useRef(0);
  const inflight = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    const id = ++latest.current;
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setError(null);
    try {
      const res = await api.getAutonomyDecisions(filter || undefined, { signal: controller.signal });
      if (id === latest.current) setData(res);
    } catch (e) {
      if (id === latest.current && !(e instanceof DOMException && e.name === "AbortError")) {
        setError(e instanceof Error ? e.message : "Failed to load decisions");
      }
    } finally {
      if (id === latest.current) setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    if (user && isAdmin) load();
  }, [user, isAdmin, load]);

  useEffect(() => () => inflight.current?.abort(), []);

  function selectFilter(value: string) {
    if (value === filter) return;
    setLoading(true); // the list below belongs to the previous filter until the new one answers
    setFilter(value);
  }

  async function reverse(id: string, note?: string) {
    await api.reverseAutonomyDecision(id, note);
    setNotice("Reversed. The credit was taken back with a new entry; nothing was deleted.");
    await load();
  }

  async function simulate(scenario: string) {
    setSimulating(scenario);
    setNotice(null);
    try {
      const res = await api.simulateAutonomyEvent(scenario);
      setNotice(
        res.enqueued
          ? `Sent a ${res.delay_days}-day delay for order ${res.order_id.slice(0, 8)}. The worker will decide it in a moment.`
          : "That event was already sent.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not send the event");
    } finally {
      setSimulating(null);
    }
  }

  if (authLoading) return null;
  if (!user || !isAdmin) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState icon={Gauge} title="Access Denied" description="Autonomous decisions are for admins." />
      </div>
    );
  }

  const decisions: AutonomyDecision[] = data?.decisions ?? [];
  const scenarios = Object.entries(data?.scenarios ?? {});

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Autonomous decisions</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            What the system did on its own, what it asked about, and the budget each one drew on. An action can be reversed
            while it is reversible.
          </p>
        </div>
        {scenarios.length > 0 && (
          <div role="group" aria-label="Send a demo event" className="flex flex-wrap gap-2">
            {scenarios.map(([name, days]) => (
              <Button key={name} type="button" size="sm" variant="outline" onClick={() => simulate(name)} disabled={simulating !== null}>
                {simulating === name ? <Loader2 className="animate-spin" data-icon="inline-start" /> : <Play data-icon="inline-start" />}
                {name} delay ({days} days)
              </Button>
            ))}
          </div>
        )}
      </div>

      {data && !data.enabled && (
        <p role="status" className="rounded-lg border bg-muted/40 px-3 py-2 text-sm">
          Event autonomy is switched off (AUTONOMY_ENABLED), so the system is not acting on events. Past decisions are
          still listed and can be reversed.
        </p>
      )}
      {notice && (
        <p role="status" className="rounded-lg border bg-muted/40 px-3 py-2 text-sm">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      {data && data.budgets.length > 0 && (
        <section aria-label="Budgets" className="grid gap-3 sm:grid-cols-2">
          {data.budgets.map((b) => (
            <BudgetMeter key={b.budget_key} budget={b} />
          ))}
        </section>
      )}

      <div role="group" aria-label="Filter decisions" className="flex flex-wrap gap-1">
        {FILTERS.map((f) => (
          <Button
            key={f.value}
            type="button"
            size="sm"
            variant={filter === f.value ? "default" : "outline"}
            aria-pressed={filter === f.value}
            onClick={() => selectFilter(f.value)}
          >
            {f.label}
          </Button>
        ))}
      </div>

      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 className="size-6 animate-spin text-muted-foreground" aria-label="Loading" />
        </div>
      ) : decisions.length === 0 ? (
        <EmptyState
          icon={Gauge}
          title="No decisions yet"
          description="When an event such as a delayed shipment arrives, the system's decision and the budget it used appear here."
        />
      ) : (
        <div className="space-y-4">
          {decisions.map((d) => (
            <DecisionCard key={d.id} decision={d} onReverse={reverse} />
          ))}
        </div>
      )}
    </div>
  );
}
