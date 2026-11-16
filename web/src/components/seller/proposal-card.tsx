"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StatusBadge, type SemanticTone } from "@/components/ui/status-badge";
import { formatDateTime, formatPrice } from "@/lib/format";
import type { Decimalish, ProposalLine, ProposalLineDecision, SellerProposal } from "@/lib/api";

type Choice = "accept" | "edit" | "reject";

const REASON_TEXT: Record<string, string> = {
  invalid_price: "Not a valid price",
  missing_cost: "No unit cost on record",
  margin_below_floor: "Margin would fall below the floor",
  daily_change_cap_exceeded: "More than the daily price-change limit",
  active_promotion: "Product is in an active promotion",
  invalid_quantity: "Not a valid quantity",
  batch_too_large: "Batch is too large",
  restock_quantity_exceeded: "More units than one restock may order",
  restock_value_exceeded: "Order value is over the restock limit",
  edit_beyond_proposal: "The edit goes past what was proposed",
  price_moved: "The price changed after this was proposed",
  restock_already_scheduled: "Stock is already on order",
};

const LINE_TONE: Record<ProposalLine["status"], SemanticTone> = {
  proposed: "neutral",
  accepted: "neutral",
  edited: "neutral",
  rejected: "neutral",
  blocked: "warning",
  executed: "success",
  failed: "destructive",
};

/** A backend decimal (a number, or a string such as "37.49") as a number; null when absent or not a finite number. */
export function toNumber(value: Decimalish | null | undefined): number | null {
  if (value == null) return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

export function describeChange(line: ProposalLine, value: Decimalish | null = line.proposed_value): string {
  const to = toNumber(value);
  if (line.field === "price") return `${formatPrice(toNumber(line.current_value))} → ${formatPrice(to)}`;
  return `Order ${to ?? 0} units (${toNumber(line.current_value) ?? 0} on hand)`;
}

/**
 * The values an edit may take, the same rule the server enforces: a restock quantity may be lowered, never raised;
 * a price stays between the proposed price and the price it started from. `null` where a bound is unknown.
 */
export function editBounds(line: ProposalLine): { min: number; max: number } | null {
  const proposed = toNumber(line.proposed_value);
  if (proposed == null) return null;
  if (line.field === "restock_quantity") return { min: 1, max: proposed };
  const current = toNumber(line.current_value) ?? proposed;
  return { min: Math.min(current, proposed), max: Math.max(current, proposed) };
}

/** An edited price equal to the current one changes nothing; the server refuses it ("reject the line instead"). */
export function isNoChangeEdit(line: ProposalLine, value: string | number | null | undefined): boolean {
  if (line.field !== "price") return false;
  const n = toNumber(value);
  const current = toNumber(line.current_value);
  return n != null && current != null && Math.round(n * 100) === Math.round(current * 100);
}

export function reasonText(code: string): string {
  return REASON_TEXT[code] ?? code.replaceAll("_", " ");
}

function ChoiceButtons({
  line,
  choice,
  onChoose,
}: {
  line: ProposalLine;
  choice: Choice;
  onChoose: (c: Choice) => void;
}) {
  const blocked = line.status === "blocked";
  return (
    <div role="group" aria-label={`Decision for ${line.product_name}`} className="flex flex-wrap gap-1">
      {(["accept", "edit", "reject"] as const).map((c) => (
        <Button
          key={c}
          type="button"
          size="sm"
          variant={choice === c ? "default" : "outline"}
          aria-pressed={choice === c}
          disabled={blocked && c !== "reject"}
          onClick={() => onChoose(c)}
        >
          {c === "accept" ? "Accept" : c === "edit" ? "Edit" : "Reject"}
        </Button>
      ))}
    </div>
  );
}

export function ProposalCard({
  proposal,
  onDecide,
}: {
  proposal: SellerProposal;
  onDecide: (id: string, lines: ProposalLineDecision[]) => Promise<void>;
}) {
  const open = proposal.status === "pending_review";
  const [choices, setChoices] = useState<Record<string, Choice>>(() =>
    Object.fromEntries(proposal.lines.map((l) => [l.id, l.status === "blocked" ? "reject" : "accept"])),
  );
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(proposal.lines.map((l) => [l.id, String(l.proposed_value)])),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const invalid = proposal.lines.some((l) => {
    if (choices[l.id] !== "edit") return false;
    const n = toNumber(values[l.id]);
    if (n == null || n <= 0 || (l.field === "restock_quantity" && !Number.isInteger(n))) return true;
    if (isNoChangeEdit(l, values[l.id])) return true;
    const bounds = editBounds(l);
    return bounds != null && (n < bounds.min || n > bounds.max);
  });

  async function submit() {
    setBusy(true);
    setError(null);
    const decisions: ProposalLineDecision[] = proposal.lines.map((l): ProposalLineDecision => {
      const choice = choices[l.id];
      return choice === "edit"
        ? { line_id: l.id, decision: "edit", value: values[l.id] }
        : { line_id: l.id, decision: choice };
    });
    try {
      await onDecide(proposal.id, decisions);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not apply the decisions");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          {proposal.kind === "mixed" ? "Markdowns and restocks" : proposal.kind === "markdown" ? "Markdowns" : "Restocks"}
          <StatusBadge
            label={open ? "Awaiting your review" : proposal.status.replace("_", " ")}
            tone={open ? "warning" : proposal.status === "executed" ? "success" : "neutral"}
          />
        </CardTitle>
        <CardDescription>
          Suggested {formatDateTime(proposal.created_at)} ({proposal.trigger})
          {open && proposal.due_at ? ` · review by ${formatDateTime(proposal.due_at)}` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Product</TableHead>
                <TableHead>Change</TableHead>
                <TableHead>Why</TableHead>
                <TableHead>{open ? "Decision" : "Outcome"}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {proposal.lines.map((line) => (
                <TableRow key={line.id}>
                  <TableCell className="font-medium">{line.product_name}</TableCell>
                  <TableCell className="whitespace-nowrap">
                    {describeChange(line, open ? line.proposed_value : (line.final_value ?? line.proposed_value))}
                  </TableCell>
                  <TableCell className="max-w-sm whitespace-normal text-sm text-muted-foreground">
                    {line.rationale}
                    {line.status === "blocked" && line.guard && (
                      <span className="mt-1 block text-foreground">
                        Blocked: {line.guard.reasons.map(reasonText).join("; ")}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    {open ? (
                      <div className="space-y-2">
                        <ChoiceButtons
                          line={line}
                          choice={choices[line.id]}
                          onChoose={(c) => setChoices((p) => ({ ...p, [line.id]: c }))}
                        />
                        {choices[line.id] === "edit" && (
                          <Input
                            type="number"
                            inputMode="decimal"
                            min={editBounds(line)?.min ?? 0}
                            max={editBounds(line)?.max}
                            step={line.field === "price" ? "0.01" : "1"}
                            aria-label={`Edited ${line.field === "price" ? "price" : "quantity"} for ${line.product_name}`}
                            value={values[line.id]}
                            onChange={(e) => setValues((p) => ({ ...p, [line.id]: e.target.value }))}
                          />
                        )}
                        {choices[line.id] === "edit" && isNoChangeEdit(line, values[line.id]) && (
                          <p role="note" className="text-xs text-muted-foreground">
                            That is the current price: no change — reject the line instead.
                          </p>
                        )}
                      </div>
                    ) : (
                      <StatusBadge label={line.status} tone={LINE_TONE[line.status]} />
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {open && (
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              Every change is checked against the margin, daily-change and promotion limits again when it is applied.
            </p>
            <Button type="button" onClick={submit} disabled={busy || invalid}>
              {busy && <Loader2 className="animate-spin" data-icon="inline-start" />}
              Apply decisions
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
