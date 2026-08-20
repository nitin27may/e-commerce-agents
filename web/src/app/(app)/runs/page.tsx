"use client";

import { useEffect, useState } from "react";
import { api, type RunEntry } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Activity,
  ChevronDown,
  Check,
  X,
  Clock,
  Zap,
  ExternalLink,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";

type CheckpointData = Awaited<ReturnType<typeof api.getRunCheckpoints>>;

export default function RunsPage() {
  const [entries, setEntries] = useState<RunEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  // Keyed by run id. Fetched eagerly for every visible row, not lazily on
  // expand — a lazy fetch means the "Needs approval" badge below can't
  // show until a row has already been opened once, defeating its purpose
  // as something to scan the list for. Each request is a single indexed
  // lookup; this page is a low-traffic admin surface, not a hot path.
  const [checkpoints, setCheckpoints] = useState<Record<string, CheckpointData>>({});
  const limit = 20;

  useEffect(() => {
    setLoading(true);
    api
      .getRuns({ limit, offset: page * limit })
      .then((r) => {
        setEntries(r.entries);
        setTotal(r.total);
        setLoading(false);
        setCheckpoints({});
        Promise.allSettled(r.entries.map((e) => api.getRunCheckpoints(e.id))).then((results) => {
          setCheckpoints((prev) => {
            const next = { ...prev };
            results.forEach((res, i) => {
              if (res.status === "fulfilled") next[r.entries[i].id] = res.value;
            });
            return next;
          });
        });
      })
      .catch(() => {
        setError("Failed to load runs");
        setLoading(false);
      });
  }, [page]);

  function refreshCheckpoints(runId: string) {
    api
      .getRunCheckpoints(runId)
      .then((data) => setCheckpoints((prev) => ({ ...prev, [runId]: data })))
      .catch(() => {});
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Agent Runs</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Recent orchestrator → specialist → tool execution traces
          </p>
        </div>
        {total > 0 && (
          <Badge variant="outline" className="text-xs">
            {total} total
          </Badge>
        )}
      </div>

      {loading && (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-16 animate-pulse rounded-lg bg-muted/60" />
          ))}
        </div>
      )}

      {error && (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            {error}
          </CardContent>
        </Card>
      )}

      {!loading && !error && entries.length === 0 && (
        <Card>
          <CardContent className="py-16 text-center">
            <Activity className="mx-auto mb-3 size-8 text-muted-foreground/40" />
            <p className="font-medium text-foreground">No runs yet</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Send a message in Chat to see agent execution traces here.
            </p>
          </CardContent>
        </Card>
      )}

      {!loading && !error && entries.length > 0 && (
        <div className="space-y-2">
          {entries.map((entry) => (
            <RunRow
              key={entry.id}
              entry={entry}
              checkpointData={checkpoints[entry.id]}
              onResumed={() => refreshCheckpoints(entry.id)}
            />
          ))}
        </div>
      )}

      {total > limit && (
        <div className="flex items-center justify-center gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
          >
            Previous
          </Button>
          <span className="text-xs text-muted-foreground">
            Page {page + 1} of {Math.ceil(total / limit)}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={(page + 1) * limit >= total}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </Button>
        </div>
      )}
    </div>
  );
}

function RunRow({
  entry,
  checkpointData,
  onResumed,
}: {
  entry: RunEntry;
  checkpointData: CheckpointData | undefined;
  onResumed: () => void;
}) {
  const [open, setOpen] = useState(false);
  const hasSteps = entry.steps.length > 0;

  // Workflow modes (workflow:pre-purchase, workflow:return-replace,
  // group-chat) don't produce agent_execution_steps rows — only "tool"
  // mode's log_execution_step() calls do — so hasSteps alone would hide
  // the expand affordance for exactly the runs most likely to have a
  // pending approval. checkpointData comes from the parent (fetched
  // eagerly for every visible row, not lazily on expand — see
  // RunsPage's comment on why).
  const [resuming, setResuming] = useState<"approve" | "reject" | null>(null);
  const [resumeError, setResumeError] = useState<string | null>(null);

  const agentsInvolved = Array.from(
    new Set(entry.steps.map((s) => s.tool_name.split(":")[0]).filter(Boolean))
  );

  async function handleResume(approved: boolean) {
    setResuming(approved ? "approve" : "reject");
    setResumeError(null);
    try {
      await api.resumeRun(entry.id, approved);
      onResumed();
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : "Resume failed.");
    } finally {
      setResuming(null);
    }
  }

  const hitl = checkpointData?.hitl_request;
  const pendingApproval = hitl?.status === "pending";

  return (
    <Card className={cn("overflow-hidden", pendingApproval && "ring-1 ring-amber-400/60")}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-start gap-3 p-4 text-left hover:bg-muted/30 transition-colors"
      >
        <div className="mt-0.5 shrink-0">
          {entry.status === "success" ? (
            <Check className="size-4 text-emerald-500" />
          ) : (
            <X className="size-4 text-destructive" />
          )}
        </div>

        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-foreground">
            {entry.input_summary ?? "—"}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Clock className="size-3" />
              {new Date(entry.created_at).toLocaleString()}
            </span>
            {entry.duration_ms > 0 && (
              <span className="flex items-center gap-1">
                <Zap className="size-3" />
                {entry.duration_ms}ms
              </span>
            )}
            {entry.tokens_in + entry.tokens_out > 0 && (
              <span>{entry.tokens_in + entry.tokens_out} tokens</span>
            )}
            {agentsInvolved.length > 0 && (
              <span className="text-muted-foreground/60">
                {agentsInvolved.join(" → ")}
              </span>
            )}
            {entry.trace_id && (
              <a
                href={`http://localhost:18888`}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="flex items-center gap-0.5 text-primary hover:underline"
              >
                Aspire <ExternalLink className="size-2.5" />
              </a>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {pendingApproval && (
            <Badge className="bg-amber-500 text-white text-xs">Needs approval</Badge>
          )}
          {hasSteps && (
            <Badge variant="outline" className="text-xs">
              {entry.steps.length} step{entry.steps.length !== 1 ? "s" : ""}
            </Badge>
          )}
          <ChevronDown
            className={cn(
              "size-4 text-muted-foreground transition-transform",
              open && "rotate-180"
            )}
          />
        </div>
      </button>

      {open && (
        <div className="border-t bg-muted/10">
          {hasSteps && (
            <ol className="divide-y text-xs">
              {entry.steps.map((step, i) => (
                <StepDetailRow key={i} step={step} />
              ))}
            </ol>
          )}

          {checkpointData === undefined && (
            <p className="px-4 py-3 text-xs text-muted-foreground">Loading checkpoints…</p>
          )}

          {hitl && (
            <div className="space-y-2 border-t px-4 py-3 text-xs">
              <p className="font-medium text-foreground">
                Return approval — {hitl.status}
                {hitl.payload?.order_id ? ` (order ${hitl.payload.order_id})` : ""}
              </p>
              {pendingApproval ? (
                <>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      disabled={resuming !== null}
                      onClick={() => handleResume(true)}
                    >
                      {resuming === "approve" && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
                      Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={resuming !== null}
                      onClick={() => handleResume(false)}
                    >
                      {resuming === "reject" && <Loader2 className="mr-1.5 size-3.5 animate-spin" />}
                      Reject
                    </Button>
                  </div>
                  {resumeError && <p className="text-destructive">{resumeError}</p>}
                </>
              ) : (
                <p className="text-muted-foreground">
                  Resolved {hitl.responded_at ? new Date(hitl.responded_at).toLocaleString() : ""}
                </p>
              )}
            </div>
          )}

          {!hasSteps && checkpointData !== undefined && !hitl && (
            <p className="px-4 py-3 text-xs text-muted-foreground">No further detail for this run.</p>
          )}
        </div>
      )}
    </Card>
  );
}

function StepDetailRow({ step }: { step: RunEntry["steps"][number] }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = step.tool_input || step.tool_output;

  // tool_name stored as "agent:tool" in DB (e.g. "product-discovery:search_products")
  const colonIdx = step.tool_name.indexOf(":");
  const agentLabel = colonIdx > 0 ? step.tool_name.slice(0, colonIdx) : "orchestrator";
  const toolLabel = colonIdx > 0 ? step.tool_name.slice(colonIdx + 1) : step.tool_name;

  return (
    <li>
      <button
        type="button"
        disabled={!hasDetail}
        onClick={() => setExpanded((e) => !e)}
        className={cn(
          "flex w-full items-center gap-2 px-4 py-2 text-left",
          hasDetail ? "cursor-pointer hover:bg-muted/40" : "cursor-default"
        )}
      >
        <span className="w-5 text-center text-muted-foreground/40">{step.step_index + 1}</span>
        {hasDetail ? (
          <ChevronDown
            className={cn(
              "size-3 shrink-0 text-muted-foreground/50 transition-transform",
              expanded && "rotate-180"
            )}
          />
        ) : (
          <span className="size-3 shrink-0" />
        )}
        <span className="rounded bg-primary/10 px-1.5 py-0.5 font-medium text-primary">
          {agentLabel}
        </span>
        <span className="truncate font-mono text-foreground/80">{toolLabel}</span>
        {step.status === "error" ? (
          <X className="size-3 shrink-0 text-destructive" />
        ) : (
          <Check className="size-3 shrink-0 text-emerald-500" />
        )}
        <span className="ml-auto shrink-0 text-muted-foreground">{step.duration_ms}ms</span>
      </button>

      {expanded && hasDetail && (
        <div className="space-y-2 border-t bg-muted/20 px-4 py-2">
          {step.tool_input && (
            <JsonBlock label="Input" value={step.tool_input} />
          )}
          {step.tool_output && (
            <JsonBlock label="Output" value={step.tool_output} />
          )}
        </div>
      )}
    </li>
  );
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  const text =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const truncated = text.length > 1200;
  const display = truncated ? text.slice(0, 1200) + "\n…" : text;

  return (
    <div>
      <p className="mb-0.5 text-xs font-medium text-muted-foreground">{label}</p>
      <pre className="max-h-48 overflow-auto rounded bg-background/60 p-2 text-[10px] leading-relaxed text-foreground/80 ring-1 ring-border/60">
        {display}
      </pre>
    </div>
  );
}
