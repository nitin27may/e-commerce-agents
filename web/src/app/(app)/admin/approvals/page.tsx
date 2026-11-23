"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { api, type ApprovalDetail, type ApprovalSummary } from "@/lib/api";
import { toast } from "@/lib/toast";
import { Badge } from "@/components/ui/badge";
import { TONE_BADGE_CLASS } from "@/components/ui/status-badge";
import { Button } from "@/components/ui/button";
import { CheckCircle, XCircle, Clock, ShieldAlert, Loader2, RefreshCw, User, Route } from "lucide-react";

// The staff inbox. It reads the unified approvals API (GET /api/approvals), so it lists every
// approval the signed-in reviewer may decide, whatever raised it: a gated tool call, a paused
// workflow (a return), or an event the system could not act on alone. The server decides what a
// reviewer sees (support: what is routed to support; admin: everything) and who may decide.

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const KIND_LABELS: Record<string, string> = {
  return_approval: "Return",
  refund_approval: "Refund",
  cancel_order: "Cancel order",
  process_refund: "Refund",
  initiate_return: "Return",
  modify_order: "Modify order",
  place_backorder: "Backorder",
  autonomous_action: "Autonomous action",
};

const SOURCE_LABELS: Record<string, string> = {
  tool: "Tool call",
  workflow: "Workflow",
  event: "Event",
};

const OPEN_STATUSES = new Set(["pending_review", "needs_info"]);

const STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  pending_confirm: { label: "Awaiting customer", className: TONE_BADGE_CLASS.warning },
  pending_review: { label: "Pending review", className: TONE_BADGE_CLASS.warning },
  needs_info: { label: "Needs info", className: TONE_BADGE_CLASS.warning },
  approved: { label: "Approved", className: TONE_BADGE_CLASS.success },
  approved_with_edits: { label: "Approved with edits", className: TONE_BADGE_CLASS.success },
  executed: { label: "Executed", className: TONE_BADGE_CLASS.success },
  rejected: { label: "Rejected", className: TONE_BADGE_CLASS.destructive },
  expired: { label: "Expired", className: TONE_BADGE_CLASS.destructive },
  failed: { label: "Failed", className: TONE_BADGE_CLASS.destructive },
};

function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind.replaceAll("_", " ");
}

function formatTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatAmount(amount: ApprovalSummary["amount"]): string | null {
  if (amount === null || amount === undefined || amount === "") return null;
  const n = Number(amount);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : String(amount);
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ApprovalsPage() {
  const router = useRouter();
  const { user, isStaff, isLoading: authLoading } = useAuth();

  const [approvals, setApprovals] = useState<ApprovalSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const [processing, setProcessing] = useState<Set<string>>(new Set());
  const [details, setDetails] = useState<Record<string, ApprovalDetail | "loading" | "error">>({});

  useEffect(() => {
    if (!authLoading && !user) router.replace("/login");
  }, [user, authLoading, router]);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api.listApprovals(filter === "pending" ? undefined : "all");
      setApprovals(data?.approvals ?? []);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load approvals");
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useLoadEffect(load, Boolean(user && isStaff));

  async function showDetail(id: string) {
    if (details[id] && details[id] !== "error") return;
    setDetails((d) => ({ ...d, [id]: "loading" }));
    try {
      const detail = await api.getApproval(id);
      setDetails((d) => ({ ...d, [id]: detail }));
    } catch {
      setDetails((d) => ({ ...d, [id]: "error" }));
    }
  }

  async function decide(approval: ApprovalSummary, decision: "approve" | "reject") {
    setProcessing((p) => new Set(p).add(approval.id));
    try {
      await api.decideApproval(approval.id, decision);
      const label = kindLabel(approval.kind);
      if (decision === "approve") toast.success(`Approved: ${label}`);
      else toast(`Rejected: ${label}`);
      await load();
    } catch (err) {
      // The server's reason is the useful part: routed to a manager, already decided, own request.
      toast.error(decision === "approve" ? "Could not approve" : "Could not reject", {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setProcessing((p) => {
        const next = new Set(p);
        next.delete(approval.id);
        return next;
      });
    }
  }

  if (authLoading) return null;

  if (!isStaff) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <div className="mx-auto flex size-16 items-center justify-center rounded-full bg-destructive/10">
            <ShieldAlert className="size-8 text-destructive" />
          </div>
          <h2 className="mt-4 text-lg font-semibold">Access Denied</h2>
          <p className="mt-1 text-sm text-muted-foreground">Staff access required (support or admin).</p>
        </div>
      </div>
    );
  }

  const pendingCount = approvals.filter((a) => OPEN_STATUSES.has(a.status)).length;

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="border-b bg-card">
        <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
                <CheckCircle className="size-5 text-primary-foreground" />
              </div>
              <div>
                <h1 className="text-xl font-bold">
                  Approval Queue
                  {pendingCount > 0 && (
                    <span className="ml-2 rounded-full bg-warning/15 px-2 py-0.5 text-sm font-medium text-[color-mix(in_oklab,var(--warning)_45%,black)] dark:text-warning">
                      {pendingCount} pending
                    </span>
                  )}
                </h1>
                <p className="text-sm text-muted-foreground">
                  Returns, refunds and other agent actions waiting for a person to decide
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex rounded-lg border bg-card">
                {(["pending", "all"] as const).map((f) => (
                  <button
                    key={f}
                    type="button"
                    aria-pressed={filter === f}
                    onClick={() => setFilter(f)}
                    className={`px-3 py-1.5 text-xs font-medium capitalize transition-colors first:rounded-l-lg last:rounded-r-lg ${
                      filter === f
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {f}
                  </button>
                ))}
              </div>
              <Button variant="outline" size="sm" onClick={load} className="gap-1.5">
                <RefreshCw className="size-3.5" />
                Refresh
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
        {loading && (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="size-5 animate-spin text-primary" />
            <span className="ml-2 text-sm text-muted-foreground">Loading…</span>
          </div>
        )}

        {!loading && loadError && (
          <div role="alert" className="rounded-xl bg-destructive/10 px-4 py-3 text-sm text-destructive">
            Could not load approvals: {loadError}
          </div>
        )}

        {!loading && !loadError && approvals.length === 0 && (
          <div className="py-20 text-center">
            <CheckCircle className="mx-auto size-12 text-success" />
            <p className="mt-3 text-sm font-medium text-foreground">
              {filter === "pending" ? "No pending approvals" : "No approvals yet"}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Returns, refunds and cancellations that policy routes to you will appear here.
            </p>
          </div>
        )}

        {!loading && !loadError && approvals.length > 0 && (
          <div className="space-y-3">
            {approvals.map((approval) => {
              const statusCfg = STATUS_CONFIG[approval.status] ?? {
                label: approval.status,
                className: TONE_BADGE_CLASS.warning,
              };
              const isPending = approval.status === "pending_review";
              const isProcessing = processing.has(approval.id);
              const amount = formatAmount(approval.amount);
              const detail = details[approval.id];

              return (
                <div key={approval.id} className="rounded-xl bg-card ring-1 ring-foreground/10">
                  <div className="flex items-start gap-4 p-4">
                    <div className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
                      {OPEN_STATUSES.has(approval.status) ? (
                        <Clock className="size-4 text-warning" />
                      ) : statusCfg.className === TONE_BADGE_CLASS.destructive ? (
                        <XCircle className="size-4 text-destructive" />
                      ) : (
                        <CheckCircle className="size-4 text-success" />
                      )}
                    </div>

                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold">
                          {kindLabel(approval.kind)}
                          {amount && <span className="ml-1.5 font-normal text-muted-foreground">{amount}</span>}
                        </span>
                        <Badge variant="outline" className={statusCfg.className}>
                          {statusCfg.label}
                        </Badge>
                        <Badge variant="outline">{SOURCE_LABELS[approval.source] ?? approval.source}</Badge>
                      </div>

                      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <User className="size-3" />
                          {approval.requester_email}
                        </span>
                        {approval.route && (
                          <span className="flex items-center gap-1">
                            <Route className="size-3" />
                            Routed to {approval.route}
                          </span>
                        )}
                        <span>{formatTs(approval.created_at)}</span>
                        {approval.due_at && OPEN_STATUSES.has(approval.status) && (
                          <span>due {formatTs(approval.due_at)}</span>
                        )}
                      </div>

                      <details
                        className="mt-2"
                        onToggle={(e) => {
                          if (e.currentTarget.open) void showDetail(approval.id);
                        }}
                      >
                        <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                          Show details
                        </summary>
                        {detail === "loading" && (
                          <p className="mt-1.5 text-xs text-muted-foreground">Loading…</p>
                        )}
                        {detail === "error" && (
                          <p className="mt-1.5 text-xs text-destructive">
                            Could not load the details. It may be routed to a manager.
                          </p>
                        )}
                        {detail && typeof detail === "object" && (
                          <pre className="mt-1.5 max-h-48 overflow-auto rounded bg-muted/60 px-2 py-1.5 text-xs leading-relaxed text-foreground/70">
                            {JSON.stringify(detail.source_detail ?? {}, null, 2)}
                          </pre>
                        )}
                      </details>
                    </div>

                    {isPending && (
                      <div className="flex shrink-0 gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={isProcessing}
                          onClick={() => decide(approval, "reject")}
                          className="gap-1.5 border-destructive/30 text-destructive hover:bg-destructive/10"
                        >
                          {isProcessing ? <Loader2 className="size-3.5 animate-spin" /> : <XCircle className="size-3.5" />}
                          Reject
                        </Button>
                        <Button
                          size="sm"
                          disabled={isProcessing}
                          onClick={() => decide(approval, "approve")}
                          className="gap-1.5 bg-[color-mix(in_oklab,var(--success)_72%,black)] text-white hover:bg-[color-mix(in_oklab,var(--success)_62%,black)]"
                        >
                          {isProcessing ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            <CheckCircle className="size-3.5" />
                          )}
                          Approve
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
