"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { api, type ApprovalDetail, type ApprovalSummary } from "@/lib/api";
import { toast } from "@/lib/toast";
import { formatDateTime, formatPrice } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { StatusBadge, TONE_BADGE_CLASS, TONE_TEXT_CLASS } from "@/components/ui/status-badge";
import { PageContainer, PageHeader } from "@/components/ui/page-header";
import { AccessDenied } from "@/components/ui/access-denied";
import { EmptyState } from "@/components/empty-state";
import { ApprovalDetailView } from "@/components/admin/approval-detail";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { CheckCircle, XCircle, Clock, Loader2, RefreshCw, User, Route } from "lucide-react";

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

function formatAmount(amount: ApprovalSummary["amount"]): string | null {
  if (amount === null || amount === undefined || amount === "") return null;
  const n = Number(amount);
  return Number.isFinite(n) ? formatPrice(n) : String(amount);
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
    return <AccessDenied description="Staff access required (support or admin)." />;
  }

  const pendingCount = approvals.filter((a) => OPEN_STATUSES.has(a.status)).length;

  return (
    <PageContainer>
      <PageHeader
        icon={CheckCircle}
        title={
          <span className="flex flex-wrap items-center gap-2">
            Approvals
            {pendingCount > 0 && <StatusBadge label={`${pendingCount} pending`} tone="warning" />}
          </span>
        }
        description="Returns, refunds and other actions waiting for a person to decide"
        actions={
          <>
            <div className="flex rounded-lg border bg-card p-0.5" role="group" aria-label="Filter approvals">
              {(["pending", "all"] as const).map((f) => (
                <button
                  key={f}
                  type="button"
                  aria-pressed={filter === f}
                  onClick={() => setFilter(f)}
                  className={cn(
                    "rounded-md px-3 py-1 text-xs font-medium transition-colors",
                    filter === f
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {f === "pending" ? "Pending" : "All"}
                </button>
              ))}
            </div>
            <Button variant="outline" size="sm" onClick={load}>
              <RefreshCw data-icon="inline-start" />
              Refresh
            </Button>
          </>
        }
      />

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
          <EmptyState
            icon={CheckCircle}
            title={filter === "pending" ? "No pending approvals" : "No approvals yet"}
            description="Returns, refunds and cancellations that policy routes to you will appear here."
          />
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
                        <Clock className={cn("size-4", TONE_TEXT_CLASS.warning)} />
                      ) : statusCfg.className === TONE_BADGE_CLASS.destructive ? (
                        <XCircle className="size-4 text-destructive" />
                      ) : (
                        <CheckCircle className={cn("size-4", TONE_TEXT_CLASS.success)} />
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
                        <span>{formatDateTime(approval.created_at)}</span>
                        {approval.due_at && OPEN_STATUSES.has(approval.status) && (
                          <span>due {formatDateTime(approval.due_at)}</span>
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
                        {detail && typeof detail === "object" && <ApprovalDetailView detail={detail} />}
                      </details>
                    </div>

                    {isPending && (
                      <div className="flex shrink-0 gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={isProcessing}
                          onClick={() => decide(approval, "reject")}
                          className="border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive"
                        >
                          {isProcessing ? <Loader2 className="size-3.5 animate-spin" /> : <XCircle className="size-3.5" />}
                          Reject
                        </Button>
                        <Button
                          size="sm"
                          disabled={isProcessing}
                          onClick={() => decide(approval, "approve")}
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
    </PageContainer>
  );
}
