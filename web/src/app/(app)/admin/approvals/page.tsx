"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { toast } from "@/lib/toast";
import { Badge } from "@/components/ui/badge";
import { TONE_BADGE_CLASS } from "@/components/ui/status-badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  CheckCircle,
  XCircle,
  Clock,
  ShieldAlert,
  Loader2,
  RefreshCw,
  Wrench,
  User,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type HitlRequest = {
  id: string;
  user_email: string;
  agent_name: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  status: string;
  admin_note: string | null;
  approved_by: string | null;
  execution_result: Record<string, unknown> | null;
  created_at: string;
  resolved_at: string | null;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TOOL_LABELS: Record<string, string> = {
  cancel_order: "Cancel Order",
  process_refund: "Process Refund",
  initiate_return: "Initiate Return",
  modify_order: "Modify Order",
  place_backorder: "Place Backorder",
};

const STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  pending: {
    label: "Pending",
    className: TONE_BADGE_CLASS.warning,
  },
  approved: {
    label: "Approved",
    className: TONE_BADGE_CLASS.success,
  },
  executed: {
    label: "Executed",
    className: TONE_BADGE_CLASS.success,
  },
  denied: {
    label: "Denied",
    className: TONE_BADGE_CLASS.destructive,
  },
};

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

function inputSummary(tool: string, input: Record<string, unknown>): string {
  if (tool === "cancel_order" || tool === "process_refund" || tool === "modify_order") {
    const id = String(input.order_id ?? "").slice(0, 8);
    const reason = input.reason ? ` — "${input.reason}"` : "";
    return `Order #${id}${reason}`;
  }
  if (tool === "initiate_return") {
    const id = String(input.order_id ?? "").slice(0, 8);
    return `Order #${id}`;
  }
  if (tool === "place_backorder") {
    return `Product #${String(input.product_id ?? "").slice(0, 8)}`;
  }
  return JSON.stringify(input).slice(0, 60);
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminApprovalsPage() {
  const router = useRouter();
  const { user, isAdmin, isLoading: authLoading } = useAuth();

  const [requests, setRequests] = useState<HitlRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const [processing, setProcessing] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!authLoading && !user) router.replace("/login");
  }, [user, authLoading, router]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getHitlRequests(filter === "pending" ? "pending" : undefined);
      setRequests(data?.requests ?? []);
    } catch {
      toast.error("Failed to load approval requests");
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    if (user && isAdmin) load();
  }, [user, isAdmin, load]);

  async function handleApprove(req: HitlRequest) {
    setProcessing((p) => new Set(p).add(req.id));
    try {
      const result = await api.approveHitlRequest(req.id);
      const execResult = result?.execution_result as Record<string, unknown> | undefined;
      const success = execResult?.success;
      const msg = String(execResult?.message ?? "Action approved and executed.");
      if (success) {
        toast.success(`Approved: ${TOOL_LABELS[req.tool_name] ?? req.tool_name}`, {
          description: msg,
        });
      } else {
        toast(`Approved — but execution issue`, { description: msg });
      }
      await load();
    } catch {
      toast.error("Failed to approve request");
    } finally {
      setProcessing((p) => {
        const next = new Set(p);
        next.delete(req.id);
        return next;
      });
    }
  }

  async function handleDeny(req: HitlRequest) {
    setProcessing((p) => new Set(p).add(req.id));
    try {
      await api.denyHitlRequest(req.id);
      toast(`Denied: ${TOOL_LABELS[req.tool_name] ?? req.tool_name}`);
      await load();
    } catch {
      toast.error("Failed to deny request");
    } finally {
      setProcessing((p) => {
        const next = new Set(p);
        next.delete(req.id);
        return next;
      });
    }
  }

  if (authLoading) return null;

  if (!isAdmin) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <div className="mx-auto flex size-16 items-center justify-center rounded-full bg-destructive/10">
            <ShieldAlert className="size-8 text-destructive" />
          </div>
          <h2 className="mt-4 text-lg font-semibold">Access Denied</h2>
          <p className="mt-1 text-sm text-muted-foreground">Admin privileges required.</p>
        </div>
      </div>
    );
  }

  const pendingCount = requests.filter((r) => r.status === "pending").length;

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
                  Human-in-the-loop approval for high-stakes agent actions
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex rounded-lg border bg-card">
                {(["pending", "all"] as const).map((f) => (
                  <button
                    key={f}
                    type="button"
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

        {!loading && requests.length === 0 && (
          <div className="py-20 text-center">
            <CheckCircle className="mx-auto size-12 text-success" />
            <p className="mt-3 text-sm font-medium text-foreground">
              {filter === "pending" ? "No pending approvals" : "No requests yet"}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              High-stakes agent actions (cancel orders, refunds, returns) will appear here.
            </p>
          </div>
        )}

        {!loading && requests.length > 0 && (
          <div className="space-y-3">
            {requests.map((req) => {
              const statusCfg = STATUS_CONFIG[req.status] ?? STATUS_CONFIG.pending;
              const isPending = req.status === "pending";
              const isProcessing = processing.has(req.id);

              return (
                <div
                  key={req.id}
                  className="rounded-xl bg-card ring-1 ring-foreground/10"
                >
                  <div className="flex items-start gap-4 p-4">
                    {/* Icon */}
                    <div className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
                      {isPending ? (
                        <Clock className="size-4 text-warning" />
                      ) : req.status === "denied" ? (
                        <XCircle className="size-4 text-destructive" />
                      ) : (
                        <CheckCircle className="size-4 text-success" />
                      )}
                    </div>

                    {/* Main content */}
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-sm">
                          {TOOL_LABELS[req.tool_name] ?? req.tool_name}
                        </span>
                        <Badge variant="outline" className={statusCfg.className}>
                          {statusCfg.label}
                        </Badge>
                      </div>

                      {/* Summary */}
                      <p className="mt-1 text-sm text-muted-foreground">
                        {inputSummary(req.tool_name, req.tool_input)}
                      </p>

                      {/* Meta row */}
                      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <User className="size-3" />
                          {req.user_email}
                        </span>
                        <span className="flex items-center gap-1">
                          <Wrench className="size-3" />
                          {req.agent_name}
                        </span>
                        <span>{formatTs(req.created_at)}</span>
                        {req.resolved_at && (
                          <span>→ resolved {formatTs(req.resolved_at)}</span>
                        )}
                        {req.approved_by && (
                          <span>by {req.approved_by}</span>
                        )}
                      </div>

                      {/* Tool input detail */}
                      <details className="mt-2">
                        <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                          Show input
                        </summary>
                        <pre className="mt-1.5 max-h-32 overflow-auto rounded bg-muted/60 px-2 py-1.5 text-xs leading-relaxed text-foreground/70">
                          {JSON.stringify(req.tool_input, null, 2)}
                        </pre>
                      </details>

                      {/* Execution result */}
                      {req.execution_result && (
                        <div className="mt-2 rounded-md bg-success/10 px-3 py-1.5 text-xs text-[color-mix(in_oklab,var(--success)_62%,black)] dark:text-success">
                          {String(req.execution_result.message ?? JSON.stringify(req.execution_result))}
                        </div>
                      )}
                    </div>

                    {/* Action buttons */}
                    {isPending && (
                      <div className="flex shrink-0 gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={isProcessing}
                          onClick={() => handleDeny(req)}
                          className="gap-1.5 border-destructive/30 text-destructive hover:bg-destructive/10"
                        >
                          {isProcessing ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            <XCircle className="size-3.5" />
                          )}
                          Deny
                        </Button>
                        <Button
                          size="sm"
                          disabled={isProcessing}
                          onClick={() => handleApprove(req)}
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
