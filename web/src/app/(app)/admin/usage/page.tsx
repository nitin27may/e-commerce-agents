"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { Separator } from "@/components/ui/separator";
import { StatCard } from "@/components/ui/stat-card";
import { SectionHeader } from "@/components/ui/section-header";
import { ChartContainer, CHART_COLORS } from "@/components/ui/chart";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  Activity,
  ArrowDownToLine,
  ArrowUpFromLine,
  Clock,
  Coins,
  Loader2,
  ShieldAlert,
  BarChart3,
} from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AgentUsage {
  agent_id: string;
  agent_name: string;
  invocations: number;
  tokens_in: number;
  tokens_out: number;
  avg_duration_ms: number;
  cost_usd: number;
}

interface DailyTrend {
  date: string;
  invocations: number;
  tokens_in: number;
  tokens_out: number;
}

interface UsageStats {
  total_invocations: number;
  total_tokens_in: number;
  total_tokens_out: number;
  total_cost_usd: number;
  active_agents: number;
  pending_requests: number;
  avg_duration_ms: number;
  per_agent: AgentUsage[];
  daily_trend: DailyTrend[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatNumber(n: number | undefined | null): string {
  if (n == null) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function formatCost(usd: number | undefined | null): string {
  if (!usd) return "—";
  return `$${usd.toFixed(usd < 1 ? 4 : 2)}`;
}

function formatDuration(ms: number | undefined | null): string {
  if (ms == null) return "0ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function formatDate(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString("en-US", {
      weekday: "short",
      month: "short",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
}

function shortDate(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
}

function InlineBar({
  value,
  max,
  className,
}: {
  value: number;
  max: number;
  className?: string;
}) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="h-1.5 w-full rounded-full bg-muted">
      <div
        className={`h-1.5 rounded-full transition-all ${className ?? "bg-primary"}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminUsagePage() {
  const router = useRouter();
  const { user, isAdmin, isLoading: authLoading } = useAuth();

  const [stats, setStats] = useState<UsageStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
    }
  }, [user, authLoading, router]);

  const loadStats = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const raw = await api.getUsageStats();
      const overall = raw.overall || {};
      setStats({
        total_invocations: overall.total_requests ?? 0,
        total_tokens_in: overall.total_tokens_in ?? 0,
        total_tokens_out: overall.total_tokens_out ?? 0,
        total_cost_usd: overall.total_cost_usd ?? 0,
        active_agents: raw.by_agent?.length ?? 0,
        pending_requests: overall.pending_requests ?? 0,
        avg_duration_ms: overall.avg_duration_ms ?? 0,
        per_agent: (raw.by_agent || []).map((a: Record<string, unknown>) => ({
          agent_id: a.agent_name,
          agent_name: a.agent_name as string,
          invocations: (a.request_count ?? 0) as number,
          tokens_in: (a.tokens_in ?? 0) as number,
          tokens_out: (a.tokens_out ?? 0) as number,
          avg_duration_ms: (a.avg_duration_ms ?? 0) as number,
          cost_usd: (a.cost_usd ?? 0) as number,
        })),
        daily_trend: (raw.daily || []).map((d: Record<string, unknown>) => ({
          date: d.date as string,
          invocations: (d.request_count ?? 0) as number,
          tokens_in: (d.tokens_in ?? 0) as number,
          tokens_out: (d.tokens_out ?? 0) as number,
        })),
      });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load usage stats",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user && isAdmin) loadStats();
  }, [user, isAdmin, loadStats]);

  if (authLoading) return null;

  if (!isAdmin) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="text-center">
          <div className="mx-auto flex size-16 items-center justify-center rounded-full bg-destructive/10">
            <ShieldAlert className="size-8 text-destructive" />
          </div>
          <h2 className="mt-4 text-lg font-semibold">Access Denied</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            You do not have admin privileges to view this page.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="mb-8 flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-primary">
          <BarChart3 className="size-5 text-primary-foreground" />
        </div>
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Usage Analytics</h1>
          <p className="text-sm text-muted-foreground">
            Detailed token and invocation metrics across all agents
          </p>
        </div>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="size-6 animate-spin text-primary" />
          <span className="ml-2 text-sm text-muted-foreground">
            Loading usage data…
          </span>
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {!loading && !error && stats && (
        <div className="space-y-8">
          {/* Summary cards */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <StatCard
              label="Total Invocations"
              value={formatNumber(stats.total_invocations)}
              icon={Activity}
              hint={`${stats.active_agents} active agents`}
            />
            <StatCard
              label="Tokens In"
              value={formatNumber(stats.total_tokens_in)}
              icon={ArrowDownToLine}
            />
            <StatCard
              label="Tokens Out"
              value={formatNumber(stats.total_tokens_out)}
              icon={ArrowUpFromLine}
            />
            <StatCard
              label="Est. Cost"
              value={formatCost(stats.total_cost_usd)}
              icon={Coins}
              hint="Rows with a recorded cost"
            />
            <StatCard
              label="Avg Duration"
              value={formatDuration(stats.avg_duration_ms)}
              icon={Clock}
            />
          </div>

          {/* Daily trend chart */}
          <div className="rounded-xl bg-card p-4 ring-1 ring-foreground/10">
            <SectionHeader
              eyebrow="Last 7 days"
              title="Daily Activity"
              description="Agent invocations per day."
            />
            {stats.daily_trend.length === 0 ? (
              <div className="py-12 text-center text-sm text-muted-foreground">
                No trend data available yet.
              </div>
            ) : (
              <ChartContainer height={260}>
                <BarChart
                  data={stats.daily_trend.map((d) => ({
                    ...d,
                    label: shortDate(d.date),
                  }))}
                  margin={{ top: 8, right: 8, left: -16, bottom: 0 }}
                >
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="var(--border)"
                    vertical={false}
                  />
                  <XAxis
                    dataKey="label"
                    tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <YAxis
                    tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
                    tickLine={false}
                    axisLine={false}
                    allowDecimals={false}
                  />
                  <Tooltip
                    cursor={{ fill: "var(--accent)", opacity: 0.4 }}
                    contentStyle={{
                      background: "var(--popover)",
                      border: "1px solid var(--border)",
                      borderRadius: "0.5rem",
                      color: "var(--popover-foreground)",
                      fontSize: "12px",
                    }}
                  />
                  <Bar
                    dataKey="invocations"
                    name="Invocations"
                    fill={CHART_COLORS[0]}
                    radius={[4, 4, 0, 0]}
                    maxBarSize={48}
                  />
                </BarChart>
              </ChartContainer>
            )}
          </div>

          {/* Per-agent breakdown */}
          <div className="rounded-xl bg-card ring-1 ring-foreground/10">
            <div className="px-4 py-3">
              <h2 className="text-sm font-semibold">Per-Agent Breakdown</h2>
            </div>
            <Separator />
            {stats.per_agent.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                No agent usage data available.
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>Agent</TableHead>
                    <TableHead className="text-right">Invocations</TableHead>
                    <TableHead className="text-right">Tokens In</TableHead>
                    <TableHead className="text-right">Tokens Out</TableHead>
                    <TableHead className="text-right">Est. Cost</TableHead>
                    <TableHead className="text-right">Avg Duration</TableHead>
                    <TableHead className="w-[120px]">Volume</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(() => {
                    const maxInvocations = Math.max(
                      ...stats.per_agent.map((a) => a.invocations),
                      1,
                    );
                    return stats.per_agent.map((agent) => (
                      <TableRow key={agent.agent_id}>
                        <TableCell className="font-medium">
                          {agent.agent_name}
                        </TableCell>
                        <TableCell className="text-right">
                          {formatNumber(agent.invocations)}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {formatNumber(agent.tokens_in)}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {formatNumber(agent.tokens_out)}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {formatCost(agent.cost_usd)}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {formatDuration(agent.avg_duration_ms)}
                        </TableCell>
                        <TableCell>
                          <InlineBar
                            value={agent.invocations}
                            max={maxInvocations}
                          />
                        </TableCell>
                      </TableRow>
                    ));
                  })()}
                </TableBody>
              </Table>
            )}
          </div>

          {/* Daily detail table */}
          <div className="rounded-xl bg-card ring-1 ring-foreground/10">
            <div className="px-4 py-3">
              <h2 className="text-sm font-semibold">Daily Trend (Last 7 Days)</h2>
            </div>
            <Separator />
            {stats.daily_trend.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                No trend data available.
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>Date</TableHead>
                    <TableHead className="text-right">Invocations</TableHead>
                    <TableHead className="text-right">Tokens In</TableHead>
                    <TableHead className="text-right">Tokens Out</TableHead>
                    <TableHead className="text-right">Total Tokens</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {stats.daily_trend.map((day) => (
                    <TableRow key={day.date}>
                      <TableCell className="font-medium">
                        {formatDate(day.date)}
                      </TableCell>
                      <TableCell className="text-right">
                        {formatNumber(day.invocations)}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">
                        {formatNumber(day.tokens_in)}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">
                        {formatNumber(day.tokens_out)}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">
                        {formatNumber(day.tokens_in + day.tokens_out)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
