"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, PackageCheck, RefreshCw } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { api, type ReturnInspection } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";

type Resolution = "refund" | "store_credit" | "replacement";

const RESOLUTIONS: { value: Resolution; label: string }[] = [
  { value: "refund", label: "Refund to the original payment" },
  { value: "store_credit", label: "Store credit" },
  { value: "replacement", label: "Replacement (as store credit toward it)" },
];

function money(n: number): string {
  return `$${n.toFixed(2)}`;
}

function daysSince(iso: string): number {
  return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000));
}

function InspectionCard({ item, onDone }: { item: ReturnInspection; onDone: (message: string) => void }) {
  const [resolution, setResolution] = useState<Resolution>("refund");
  const [amount, setAmount] = useState("");
  const [condition, setCondition] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"accept" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const waited = daysSince(item.waiting_since);
  const overdue = waited >= item.overdue_after_days;

  async function decide(outcome: "accept" | "reject") {
    setError(null);
    const parsed = amount.trim() === "" ? undefined : Number(amount);
    if (outcome === "accept" && parsed !== undefined) {
      if (!Number.isFinite(parsed) || parsed <= 0) {
        setError("Enter an amount above zero, or leave it empty to pay the original amount.");
        return;
      }
      if (parsed > item.refund_amount) {
        setError(`The amount cannot be higher than the original ${money(item.refund_amount)}.`);
        return;
      }
    }
    setBusy(outcome);
    try {
      const res = await api.inspectReturn(item.return_id, {
        outcome,
        ...(outcome === "accept" ? { resolution, ...(parsed !== undefined ? { amount: parsed } : {}) } : {}),
        condition: condition.trim() || undefined,
        note: note.trim() || undefined,
      });
      onDone(
        outcome === "accept"
          ? `Accepted: ${money(res.paid)} as ${(res.resolution ?? "").replace("_", " ")}.`
          : "Rejected: the customer is told, nothing is paid.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "The decision could not be recorded.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <li className="space-y-3 rounded-lg border p-4 text-sm" data-testid="inspection-card">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-medium">
          {item.customer_name} <span className="font-normal text-muted-foreground">({item.customer_email})</span>
        </p>
        <p className={overdue ? "font-medium text-destructive" : "text-muted-foreground"}>
          {overdue ? `Overdue: waiting ${waited} days` : `Waiting ${waited} ${waited === 1 ? "day" : "days"}`}
        </p>
      </div>
      <p className="text-muted-foreground">
        Return {item.return_id.slice(0, 8)} · {money(item.refund_amount)} · &ldquo;{item.reason}&rdquo;
        {item.scanned_at ? ` · scanned ${new Date(item.scanned_at).toLocaleDateString()}` : ""}
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1">
          <span className="text-xs font-medium">If you accept</span>
          <select
            className="w-full rounded-md border bg-background px-2 py-1.5"
            value={resolution}
            onChange={(e) => setResolution(e.target.value as Resolution)}
            aria-label="Resolution"
          >
            {RESOLUTIONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium">Amount (empty pays {money(item.refund_amount)}; it can only go down)</span>
          <input
            className="w-full rounded-md border bg-background px-2 py-1.5"
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            aria-label="Amount"
            placeholder={item.refund_amount.toFixed(2)}
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium">Condition</span>
          <input
            className="w-full rounded-md border bg-background px-2 py-1.5"
            value={condition}
            maxLength={200}
            onChange={(e) => setCondition(e.target.value)}
            aria-label="Condition"
            placeholder="e.g. like new, damaged, used"
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium">Note</span>
          <input
            className="w-full rounded-md border bg-background px-2 py-1.5"
            value={note}
            maxLength={1000}
            onChange={(e) => setNote(e.target.value)}
            aria-label="Note"
          />
        </label>
      </div>

      <div className="flex gap-2">
        <Button size="sm" disabled={busy !== null} onClick={() => decide("accept")}>
          {busy === "accept" && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
          Accept
        </Button>
        <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => decide("reject")}>
          {busy === "reject" && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
          Reject
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
    </li>
  );
}

export default function ReturnInspectionsPage() {
  const { user, isLoading: authLoading, isStaff } = useAuth();
  const [items, setItems] = useState<ReturnInspection[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  // Only the newest request may write, so a slow earlier answer cannot put a finished return back on the list.
  const latest = useRef(0);

  const load = useCallback(async () => {
    const id = ++latest.current;
    try {
      const res = await api.getReturnInspections();
      if (id === latest.current) {
        setItems(res.inspections);
        setError(null);
      }
    } catch (e) {
      if (id === latest.current) setError(e instanceof Error ? e.message : "Failed to load returns");
    }
  }, []);

  useEffect(() => {
    if (user && isStaff) void load();
  }, [user, isStaff, load]);

  async function refresh() {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }

  if (authLoading) return null;
  if (!user || !isStaff) {
    return <EmptyState icon={PackageCheck} title="Staff only" description="Only support and admins inspect returns." />;
  }

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-4 sm:p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Returns to inspect</h1>
          <p className="text-sm text-muted-foreground">
            The carrier has these. Accept one to pay it out (refund, store credit or a replacement), or reject it.
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={refresh} disabled={refreshing} aria-label="Refresh">
          <RefreshCw className={refreshing ? "size-4 animate-spin" : "size-4"} />
        </Button>
      </div>

      {notice && (
        <p role="status" className="rounded-md border bg-muted px-3 py-2 text-sm">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      {items === null && !error && <p className="text-sm text-muted-foreground">Loading returns…</p>}
      {items !== null && items.length === 0 && (
        <EmptyState icon={PackageCheck} title="Nothing to inspect" description="No return is waiting for inspection." />
      )}
      {items !== null && items.length > 0 && (
        <ul className="space-y-3">
          {items.map((item) => (
            <InspectionCard
              key={item.return_id}
              item={item}
              onDone={(message) => {
                setNotice(message);
                void load();
              }}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
