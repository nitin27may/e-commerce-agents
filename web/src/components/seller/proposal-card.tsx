"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StatusBadge, type SemanticTone } from "@/components/ui/status-badge";
import { formatDateTime, formatPrice } from "@/lib/format";
import type { ProposalLine, ProposalLineDecision, SellerProposal } from "@/lib/api";

type Choice = "accept" | "edit" | "reject";

const REASON_TEXT: Record<string, string> = {
  invalid_price: "Not a valid price",
  missing_cost: "No unit cost on record",
  margin_below_floor: "Margin would fall below the floor",
  daily_change_cap_exceeded: "More than the daily price-change limit",
  active_promotion: "Product is in an active promotion",
  invalid_quantity: "Not a valid quantity",
  batch_too_large: "Batch is too large",
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

export function describeChange(line: ProposalLine, value: number | null = line.proposed_value): string {
  if (line.field === "price") return `${formatPrice(line.current_value)} → ${formatPrice(value)}`;
  return `Order ${value ?? 0} units (${line.current_value ?? 0} on hand)`;
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
    const n = Number(values[l.id]);
    return !Number.isFinite(n) || n <= 0 || (l.field === "restock_quantity" && !Number.isInteger(n));
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
                            min="0"
                            step={line.field === "price" ? "0.01" : "1"}
                            aria-label={`Edited ${line.field === "price" ? "price" : "quantity"} for ${line.product_name}`}
                            value={values[line.id]}
                            onChange={(e) => setValues((p) => ({ ...p, [line.id]: e.target.value }))}
                          />
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
