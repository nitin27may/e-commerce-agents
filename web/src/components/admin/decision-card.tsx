"use client";

import { useState } from "react";
import { Loader2, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge, type SemanticTone } from "@/components/ui/status-badge";
import { formatDateTime, formatPrice } from "@/lib/format";
import type { AutonomyDecision, AutonomyState } from "@/lib/api";

const STATE: Record<AutonomyState, { label: string; tone: SemanticTone }> = {
  executed: { label: "Done by the system", tone: "success" },
  executed_after_approval: { label: "Done after approval", tone: "success" },
  awaiting_approval: { label: "Waiting for approval", tone: "warning" },
  approving: { label: "Being approved", tone: "info" },
  rejected: { label: "Rejected", tone: "neutral" },
  expired: { label: "Expired, nothing done", tone: "neutral" },
  failed: { label: "Approved but not done", tone: "destructive" },
  reversed: { label: "Reversed", tone: "neutral" },
  no_action: { label: "Nothing to do", tone: "neutral" },
};

const TIER: Record<string, string> = {
  auto: "Acted alone",
  auto_notify: "Acted alone, staff told",
  needs_approval: "Needed approval",
  no_action: "No action",
};

/** Fixed codes the server records as the reason a decision came out as it did. */
const REASON: Record<string, string> = {
  "budget:per_action_amount": "Over the most one action may cost",
  "budget:customer_daily_count": "This customer already received an automatic credit today",
  "budget:customer_daily_amount": "Would take this customer over their daily amount",
  "budget:order_count": "This order has already been credited",
  "budget:global_daily_count": "Today's limit on automatic actions is used up",
  "budget:global_daily_amount": "Would take today's total over its limit",
  "budget:amount_missing": "No amount could be determined",
  invalid_event_order: "The event named no valid order",
  invalid_event_delay: "The event gave no usable delay",
  order_not_found: "The order does not exist",
  order_not_eligible: "The order is not in transit",
  delay_below_minimum: "The delay is too short to earn a credit",
  approval_already_open: "A credit for this order is already waiting for approval",
};

export function reasonText(code: string): string {
  return REASON[code] ?? code;
}

/** A decimal string from the server as a display amount; "-" when absent or not a number. */
export function money(value: string | null | undefined): string {
  if (value == null || value.trim() === "") return "-";
  const n = Number(value);
  return Number.isFinite(n) ? formatPrice(n) : "-";
}

export function DecisionCard({
  decision,
  onReverse,
}: {
  decision: AutonomyDecision;
  onReverse: (id: string, note?: string) => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const state = STATE[decision.state] ?? { label: decision.state, tone: "neutral" as const };
  const drew = decision.budget.drew;
  const tripped = decision.budget.tripped ?? [];
  const label = `${decision.action.replaceAll("_", " ")} ${money(decision.applied_amount ?? decision.amount)} for ${decision.customer_email ?? "unknown customer"}`;

  async function reverse() {
    setBusy(true);
    setError(null);
    try {
      await onReverse(decision.id, note.trim() || undefined);
      setConfirming(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reverse");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card data-testid="decision-card">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <span>{label}</span>
          <StatusBadge label={state.label} tone={state.tone} />
          {decision.tier && <StatusBadge label={TIER[decision.tier] ?? decision.tier} tone="neutral" />}
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          {formatDateTime(decision.created_at)}
          {decision.order_id ? ` · order ${decision.order_id.slice(0, 8)}` : ""}
          {decision.input.event?.delay_days != null ? ` · ${decision.input.event.delay_days} day delay` : ""}
          {decision.policy_version ? ` · policy ${decision.policy_version}` : ""}
        </p>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div>
          <p className="font-medium">Why</p>
          <ul className="list-disc pl-5 text-muted-foreground">
            {decision.reasons.map((r) => (
              <li key={r}>{reasonText(r)}</li>
            ))}
          </ul>
        </div>
        <p className="text-muted-foreground">
          {drew
            ? `Drew ${money(drew.amount)} from the ${decision.budget.budget_key?.replaceAll("_", " ")} budget for ${decision.budget.day} (UTC).`
            : tripped.length > 0
              ? "Drew nothing: a limit was reached, so a person decides."
              : "Drew nothing from the budget."}
          {decision.approved_by ? ` Approved by ${decision.approved_by}.` : ""}
          {decision.reversed_by ? ` Reversed by ${decision.reversed_by} on ${formatDateTime(decision.reversed_at)}.` : ""}
        </p>
        {error && (
          <p role="alert" className="text-destructive">
            {error}
          </p>
        )}
        {decision.can_reverse &&
          (confirming ? (
            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                Reason (optional)
                <input
                  className="h-8 w-64 rounded-md border bg-background px-2 text-sm text-foreground"
                  value={note}
                  maxLength={500}
                  onChange={(e) => setNote(e.target.value)}
                />
              </label>
              <Button type="button" size="sm" variant="destructive" onClick={reverse} disabled={busy}>
                {busy ? <Loader2 className="animate-spin" data-icon="inline-start" /> : <Undo2 data-icon="inline-start" />}
                Confirm reversal
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setConfirming(false)} disabled={busy}>
                Cancel
              </Button>
            </div>
          ) : (
            <Button type="button" size="sm" variant="outline" onClick={() => setConfirming(true)}>
              <Undo2 data-icon="inline-start" />
              Reverse
            </Button>
          ))}
      </CardContent>
    </Card>
  );
}
