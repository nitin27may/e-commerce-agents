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

/** A risk review is "cleared" or "upheld" by a person, not "done" by the system, so it reads differently. */
const RISK_STATE: Partial<Record<AutonomyState, { label: string; tone: SemanticTone }>> = {
  awaiting_approval: { label: "Waiting for review", tone: "warning" },
  approving: { label: "Being cleared", tone: "info" },
  executed_after_approval: { label: "Cleared by staff", tone: "success" },
  rejected: { label: "Review rejected", tone: "neutral" },
  expired: { label: "Expired, nothing was cleared", tone: "neutral" },
  failed: { label: "Approved but not cleared", tone: "destructive" },
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
  "budget:global_monthly_count": "This month's limit on automatic actions is used up",
  "budget:global_monthly_amount": "Would take this month's total over its limit",
  "budget:amount_missing": "No amount could be determined",
  invalid_event_order: "The event named no valid order",
  invalid_event_delay: "The event gave no usable delay",
  order_not_found: "The order does not exist",
  order_not_eligible: "The order is not in transit",
  delay_below_minimum: "The delay is too short to earn a credit",
  approval_already_open: "A credit for this order is already waiting for approval",
  order_already_credited: "This order has already been credited",
  beneficiary_is_staff: "The credit is for a staff member, so a person must decide it",
  demo_user_excluded: "The demo account is never acted on",
  line_not_found: "The order line does not exist",
  line_not_for_product: "The order line is for a different product",
  line_already_adjusted: "This order line has already had a price adjustment",
  order_adjustment_limit: "This order has reached its limit of adjusted lines",
  purchase_too_old: "The purchase is too old for a price adjustment",
  price_not_lower_than_paid: "The new price is not lower than the price paid",
  invalid_event_product: "The event named no valid product",
  invalid_event_price: "The event gave no usable price",
  product_not_found: "The product does not exist",
  product_not_in_stock: "The product has no stock, so nobody was told it is back",
  recipients_over_cap: "Too many customers for the system to notify alone; a person should look",
  customer_daily_notice_cap: "This customer already received the most notices allowed today",
  recipient_is_staff: "Staff are not notified by the system",
  customer_not_found: "The customer does not exist",
  notice_needs_review: "The policy does not let this notice go out alone",
  invalid_event_item: "The event named no valid order line",
  invalid_event_quantity: "The event gave no usable quantity",
  quantity_exceeds_line: "More units were reported damaged than the order line has",
  damage_report_too_old: "The damage was reported too long after the purchase",
  line_already_remedied: "This order line has already had a remedy for damage",
  order_remedy_limit: "This order has reached its limit of remedied lines",
  remedy_below_minimum: "The remedy would come to less than one cent",
  order_already_held: "The order is already on hold",
  order_already_reviewed: "A person has already been asked to review this order",
  risk_low: "The order looks ordinary, so nothing was done",
};

const ACTION_LABEL: Record<string, string> = {
  goodwill_credit: "goodwill credit",
  price_adjustment: "price adjustment",
  back_in_stock_notice: "back-in-stock notice",
  damage_goodwill: "damaged-item remedy",
  order_risk_review: "order risk review",
  none: "no action",
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

  const isRisk = decision.action === "order_risk_review";
  const state =
    (isRisk ? RISK_STATE[decision.state] : undefined) ??
    STATE[decision.state] ?? { label: decision.state, tone: "neutral" as const };
  const drew = decision.budget.drew;
  const tripped = decision.budget.tripped ?? [];
  const actionName = ACTION_LABEL[decision.action] ?? decision.action.replaceAll("_", " ");
  // A risk review's amount is the order's total (what is at stake), not something paid: it is shown separately.
  const amountPart =
    isRisk || (decision.amount == null && decision.applied_amount == null)
      ? ""
      : ` ${money(decision.applied_amount ?? decision.amount)}`;
  const label = `${actionName}${amountPart} for ${decision.customer_email ?? "unknown customer"}`;
  const event = decision.input.event;
  const productName = event?.line?.product_name ?? event?.product_name;
  const risk = event?.risk;
  const resolutionText =
    event?.resolution === "replacement_offer"
      ? "free replacement offered"
      : event?.resolution === "credit"
        ? "store credit"
        : null;

  async function reverse() {
    setBusy(true);
    setError(null);
    try {
      await onReverse(decision.id, note.trim() || undefined);
      setConfirming(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : isRisk ? "Could not release the hold" : "Could not reverse");
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
          {event?.delay_days != null ? ` · ${event.delay_days} day delay` : ""}
          {productName ? ` · ${productName}` : ""}
          {event?.line?.paid_unit_price && event.line.new_price
            ? ` · paid ${money(event.line.paid_unit_price)}, now ${money(event.line.new_price)}`
            : ""}
          {event?.line?.damaged_quantity ? ` · ${event.line.damaged_quantity} damaged` : ""}
          {resolutionText ? ` · ${resolutionText}` : ""}
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
        {risk && (
          <div data-testid="decision-risk">
            <p className="font-medium">
              Risk score {risk.score} of 100{risk.order_total ? ` for an order of ${money(risk.order_total)}` : ""}
            </p>
            {(risk.signals ?? []).length > 0 && (
              <ul className="list-disc pl-5 text-muted-foreground">
                {(risk.signals ?? []).map((s) => (
                  <li key={s.signal}>
                    {s.signal.replaceAll("-", " ")} (+{s.points})
                  </li>
                ))}
              </ul>
            )}
            <p className="text-muted-foreground">
              {decision.hold_active
                ? "The order is on hold until a person decides. Nothing was cancelled or refunded."
                : decision.reversed_by
                  ? `The hold was released by ${decision.reversed_by} on ${formatDateTime(decision.reversed_at)}.`
                  : "The order was not held."}
            </p>
          </div>
        )}
        <p className="text-muted-foreground">
          {drew
            ? `Drew ${money(drew.amount)} from the ${decision.budget.budget_key?.replaceAll("_", " ")} budget for ${decision.budget.day} (UTC).`
            : tripped.length > 0
              ? "Drew nothing: a limit was reached, so a person decides."
              : "Drew nothing from the budget."}
          {decision.approved_by ? ` Approved by ${decision.approved_by}.` : ""}
          {decision.reversed_by && !isRisk
            ? ` Reversed by ${decision.reversed_by} on ${formatDateTime(decision.reversed_at)}.`
            : ""}
        </p>
        {decision.draft && (
          <div data-testid="decision-draft">
            <p className="font-medium">Message to the customer</p>
            <p className="text-muted-foreground">{decision.draft.body}</p>
            <p className="text-xs text-muted-foreground">
              {decision.draft.source === "llm"
                ? "Worded by the ops-responder; the amount and the decision are the engine's."
                : `Standard wording${decision.draft.fallback_reason ? ` (the draft was not used: ${decision.draft.fallback_reason})` : ""}.`}
            </p>
          </div>
        )}
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
                {isRisk ? "Confirm release" : "Confirm reversal"}
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setConfirming(false)} disabled={busy}>
                Cancel
              </Button>
            </div>
          ) : (
            <Button type="button" size="sm" variant="outline" onClick={() => setConfirming(true)}>
              <Undo2 data-icon="inline-start" />
              {isRisk ? "Release hold" : "Reverse"}
            </Button>
          ))}
      </CardContent>
    </Card>
  );
}
