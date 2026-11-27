"use client";

import { Fragment, useState, useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { asArray, asNumber } from "@/lib/guards";
import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { StatusBadge } from "@/components/ui/status-badge";
import { PageContainer, PageHeader } from "@/components/ui/page-header";
import { AccessDenied } from "@/components/ui/access-denied";
import { EmptyState } from "@/components/empty-state";
import { formatDateTime, formatDuration, formatNumber } from "@/lib/format";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  ScrollText,
  Wrench,
  Download,
  ExternalLink,
  Search,
  X,
} from "lucide-react";
import { AGENTS } from "@/lib/agents";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type AuditStep = {
  step_index: number;
  tool_name: string;
  tool_input: Record<string, unknown> | null;
  tool_output: Record<string, unknown> | null;
  status: string;
  duration_ms: number;
};

type AuditEntry = {
  id: string;
  agent_name: string;
  user_email: string | null;
  user_name: string | null;
  input_summary: string | null;
  tokens_in: number;
  tokens_out: number;
  tool_calls_count: number;
  duration_ms: number;
  status: "success" | "error";
  error_message: string | null;
  trace_id: string | null;
  model?: string | null;
  tokens_cached_in?: number | null;
  /** null = not captured; 0 = the mode made no LLM call. */
  cost_usd?: number | null;
  created_at: string;
  steps: AuditStep[];
};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 25;
const AGENT_OPTIONS = ["All agents", ...AGENTS.map((a) => a.backendName)];
const ASPIRE_BASE = "http://localhost:18888";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function exportJson(entries: AuditEntry[]) {
  const blob = new Blob([JSON.stringify(entries, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function FilterBar({
  agentFilter,
  setAgentFilter,
  statusFilter,
  setStatusFilter,
  search,
  setSearch,
}: {
  agentFilter: string;
  setAgentFilter: (v: string) => void;
  statusFilter: string;
  setStatusFilter: (v: string) => void;
  search: string;
  setSearch: (v: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-3">
      {/* Agent filter */}
      <select
        value={agentFilter}
        onChange={(e) => setAgentFilter(e.target.value)}
        className="rounded-md border bg-background px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
        aria-label="Filter by agent"
      >
        {AGENT_OPTIONS.map((a) => (
          <option key={a} value={a === "All agents" ? "" : a}>
            {a}
          </option>
        ))}
      </select>

      {/* Status filter */}
      <select
        value={statusFilter}
        onChange={(e) => setStatusFilter(e.target.value)}
        className="rounded-md border bg-background px-3 py-1.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
        aria-label="Filter by status"
      >
        <option value="">All statuses</option>
        <option value="success">Success</option>
        <option value="error">Error</option>
      </select>

      {/* Search */}
      <div className="relative flex-1 min-w-[180px]">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <input
          ref={inputRef}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search user or prompt…"
          className="h-8 w-full rounded-md border bg-background pl-8 pr-8 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
        {search && (
          <button
            type="button"
            onClick={() => {
              setSearch("");
              inputRef.current?.focus();
            }}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            <X className="size-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

function StepDetail({ step }: { step: AuditStep }) {
  const inputStr = step.tool_input
    ? JSON.stringify(step.tool_input, null, 2)
    : null;
  const outputStr = step.tool_output
    ? JSON.stringify(step.tool_output, null, 2)
    : null;

  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="inline-flex size-5 items-center justify-center rounded bg-muted text-xs font-medium text-muted-foreground">
            {step.step_index + 1}
          </span>
          <span className="font-mono text-xs font-semibold text-foreground/80">
            {step.tool_name}
          </span>
          {step.status === "error" && (
            <StatusBadge label="Error" tone="destructive" />
          )}
        </div>
        <span className="text-xs text-muted-foreground">
          {formatDuration(step.duration_ms)}
        </span>
      </div>
      {inputStr && (
        <pre className="mt-1.5 max-h-24 overflow-auto rounded bg-muted/60 px-2 py-1 pl-7 text-xs leading-relaxed text-foreground/70">
          {inputStr}
        </pre>
      )}
      {outputStr && (
        <pre className="mt-1 max-h-24 overflow-auto rounded bg-muted/40 px-2 py-1 pl-7 text-xs leading-relaxed text-muted-foreground">
          {outputStr}
        </pre>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminAuditPage() {
  const router = useRouter();
  const { user, isAdmin, isLoading: authLoading } = useAuth();

  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  // Filters
  const [agentFilter, setAgentFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  // Debounced search
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  // Reset page when filters change (adjusting state during render, not in an effect).
  const filterKey = `${agentFilter}|${statusFilter}|${debouncedSearch}`;
  const [prevFilterKey, setPrevFilterKey] = useState(filterKey);
  if (prevFilterKey !== filterKey) {
    setPrevFilterKey(filterKey);
    setPage(0);
  }

  useEffect(() => {
    if (!authLoading && !user) router.replace("/login");
  }, [user, authLoading, router]);

  const loadAudit = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getAuditLog({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        agent_name: agentFilter || undefined,
        status: statusFilter || undefined,
        search: debouncedSearch || undefined,
      });
      const list = asArray<AuditEntry>(data?.entries).map((e) => ({ ...e, steps: asArray<AuditEntry["steps"][number]>(e.steps) }));
      setEntries(list);
      setTotal(asNumber(data?.total, list.length));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load audit log",
      );
    } finally {
      setLoading(false);
    }
  }, [page, agentFilter, statusFilter, debouncedSearch]);

  useLoadEffect(loadAudit, Boolean(user && isAdmin));

  function toggleExpanded(id: string) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (authLoading) return null;

  if (!isAdmin) {
    return <AccessDenied description="Admin privileges required." />;
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const hasFilters = !!(agentFilter || statusFilter || debouncedSearch);

  return (
    <PageContainer>
      <PageHeader
        icon={ScrollText}
        title="Audit"
        description="Every agent invocation with its tool steps"
        actions={
          <Button
            variant="outline"
            size="sm"
            disabled={entries.length === 0}
            onClick={() => exportJson(entries)}
          >
            <Download data-icon="inline-start" />
            Export JSON
          </Button>
        }
      />
        <div className="rounded-xl bg-card ring-1 ring-foreground/10">
          {/* Filter bar */}
          <FilterBar
            agentFilter={agentFilter}
            setAgentFilter={setAgentFilter}
            statusFilter={statusFilter}
            setStatusFilter={setStatusFilter}
            search={search}
            setSearch={setSearch}
          />
          <Separator />

          {/* Table header row */}
          <div className="flex items-center justify-between px-4 py-2">
            <span className="text-xs text-muted-foreground">
              {loading
                ? "Loading…"
                : `${total.toLocaleString()} run${total !== 1 ? "s" : ""}${hasFilters ? " (filtered)" : ""}`}
            </span>
            {hasFilters && (
              <button
                type="button"
                onClick={() => {
                  setAgentFilter("");
                  setStatusFilter("");
                  setSearch("");
                }}
                className="text-xs text-primary hover:underline"
              >
                Clear filters
              </button>
            )}
          </div>

          {loading && (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="size-5 animate-spin text-primary" />
              <span className="ml-2 text-sm text-muted-foreground">
                Loading…
              </span>
            </div>
          )}

          {error && (
            <ErrorState inline onRetry={loadAudit} message={error} homeHref="/home" homeLabel="Back to dashboard" />
          )}

          {!loading && !error && entries.length === 0 && (
            <EmptyState
              icon={ScrollText}
              title={hasFilters ? "No matching runs" : "No runs recorded yet"}
              description={hasFilters ? "No runs match the current filters." : "Agent invocations appear here as they happen."}
              className="m-4 border-0"
            />
          )}

          {!loading && !error && entries.length > 0 && (
            <Table className="table-fixed">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-10" />
                  <TableHead className="w-[150px]">Time</TableHead>
                  <TableHead className="w-[120px]">Agent</TableHead>
                  <TableHead className="w-[190px]">User</TableHead>
                  <TableHead>Prompt</TableHead>
                  <TableHead className="w-[70px] text-right">Tokens</TableHead>
                  <TableHead className="w-[80px] text-right">Duration</TableHead>
                  <TableHead className="w-[72px]">Status</TableHead>
                  <TableHead className="w-[72px] pr-4">Trace</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {entries.map((entry) => {
                  const isExpanded = expandedIds.has(entry.id);
                  const hasSteps = entry.steps?.length > 0;

                  return (
                    <Fragment key={entry.id}>
                      <TableRow
                        aria-expanded={hasSteps ? isExpanded : undefined}
                        className={cn("text-xs", hasSteps && "cursor-pointer")}
                        onClick={() => hasSteps && toggleExpanded(entry.id)}
                        onKeyDown={(e) => {
                          if (hasSteps && e.key === "Enter") toggleExpanded(entry.id);
                        }}
                        tabIndex={hasSteps ? 0 : undefined}
                      >
                        <TableCell className="text-center">
                          {hasSteps &&
                            (isExpanded ? (
                              <ChevronDown className="mx-auto size-4 text-muted-foreground" />
                            ) : (
                              <ChevronRight className="mx-auto size-4 text-muted-foreground" />
                            ))}
                        </TableCell>
                        <TableCell className="text-muted-foreground">{formatDateTime(entry.created_at)}</TableCell>
                        <TableCell className="truncate font-mono text-foreground/80">{entry.agent_name}</TableCell>
                        <TableCell className="truncate text-muted-foreground">{entry.user_email ?? "-"}</TableCell>
                        <TableCell className="truncate text-muted-foreground" title={entry.input_summary ?? undefined}>
                          {entry.input_summary ?? "-"}
                        </TableCell>
                        <TableCell
                          className="text-right tabular-nums text-muted-foreground"
                          title={
                            entry.cost_usd == null
                              ? "Cost not captured"
                              : `${entry.model ?? "no model call"} · ${formatNumber(entry.tokens_cached_in ?? 0)} cached in · est. $${entry.cost_usd.toFixed(4)}`
                          }
                        >
                          {formatNumber(entry.tokens_in + entry.tokens_out)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          {formatDuration(entry.duration_ms)}
                        </TableCell>
                        <TableCell>
                          {entry.status === "success" ? (
                            <StatusBadge label="OK" tone="success" />
                          ) : (
                            <StatusBadge label="Error" tone="destructive" />
                          )}
                        </TableCell>
                        <TableCell className="pr-4">
                          {entry.trace_id ? (
                            <a
                              href={`${ASPIRE_BASE}/traces/${entry.trace_id}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              title={`Open trace ${entry.trace_id} in Aspire`}
                              className="inline-flex items-center gap-0.5 text-primary hover:underline"
                            >
                              <ExternalLink className="size-3" />
                              Aspire
                            </a>
                          ) : (
                            <span className="text-muted-foreground">-</span>
                          )}
                        </TableCell>
                      </TableRow>

                      {isExpanded && hasSteps && (
                        <TableRow className="hover:bg-transparent">
                          <TableCell colSpan={9} className="whitespace-normal bg-muted/40 px-4 pb-3 pt-2">
                            <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                              <Wrench className="size-3" />
                              Tool steps ({entry.steps.length})
                            </div>
                            <div className="space-y-1.5">
                              {entry.steps.map((step) => (
                                <StepDetail key={step.step_index} step={step} />
                              ))}
                            </div>
                            {entry.error_message && (
                              <p className="mt-2 rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">
                                {entry.error_message}
                              </p>
                            )}
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          )}

          {/* Pagination */}
          {!loading && total > PAGE_SIZE && (
            <>
              <Separator />
              <div className="flex items-center justify-between px-4 py-3">
                <span className="text-xs text-muted-foreground">
                  Page {page + 1} of {totalPages} &middot;{" "}
                  {total.toLocaleString()} total
                </span>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page === 0}
                    onClick={() => setPage((p) => p - 1)}
                  >
                    Previous
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page >= totalPages - 1}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    Next
                  </Button>
                </div>
              </div>
            </>
          )}
        </div>
    </PageContainer>
  );
}
