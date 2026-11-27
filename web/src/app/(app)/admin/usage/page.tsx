"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { formatDate, formatDuration, formatNumber } from "@/lib/format";
import { PageContainer, PageHeader } from "@/components/ui/page-header";
import { AccessDenied } from "@/components/ui/access-denied";
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

// Sub-dollar costs keep four decimals: a demo run costs fractions of a cent.
function formatCost(usd: number | undefined | null): string {
  if (!usd) return "-";
  return `$${usd.toFixed(usd < 1 ? 4 : 2)}`;
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
      const overall = raw.overall;
      setStats({
        total_invocations: overall.total_requests ?? 0,
        total_tokens_in: overall.total_tokens_in ?? 0,
        total_tokens_out: overall.total_tokens_out ?? 0,
        total_cost_usd: overall.total_cost_usd ?? 0,
        active_agents: raw.by_agent.length,
        pending_requests: overall.pending_requests ?? 0,
        avg_duration_ms: overall.avg_duration_ms ?? 0,
        per_agent: raw.by_agent.map((a) => ({
          agent_id: a.agent_name,
          agent_name: a.agent_name,
          invocations: a.request_count,
          tokens_in: a.tokens_in,
          tokens_out: a.tokens_out,
          avg_duration_ms: a.avg_duration_ms,
          cost_usd: a.cost_usd,
        })),
        daily_trend: raw.daily_trend.map((d) => ({
          date: d.day,
          invocations: d.request_count,
          tokens_in: 0,
          tokens_out: 0,
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

  useLoadEffect(loadStats, Boolean(user && isAdmin));

  if (authLoading) return null;

  if (!isAdmin) {
    return <AccessDenied description="You do not have admin privileges to view this page." />;
  }

  return (
    <PageContainer>
      <PageHeader
        icon={BarChart3}
        title="Usage"
        description="Invocations, tokens and cost across all agents"
      />

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
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <StatCard
              label="Invocations"
              value={formatNumber(stats.total_invocations)}
              icon={Activity}
              hint={`${stats.active_agents} active ${stats.active_agents === 1 ? "agent" : "agents"}`}
            />
            <StatCard
              label="Tokens in"
              value={formatNumber(stats.total_tokens_in)}
              icon={ArrowDownToLine}
            />
            <StatCard
              label="Tokens out"
              value={formatNumber(stats.total_tokens_out)}
              icon={ArrowUpFromLine}
            />
            <StatCard
              label="Est. cost"
              value={stats.total_cost_usd ? formatCost(stats.total_cost_usd) : "$0.00"}
              icon={Coins}
              hint={stats.total_cost_usd ? undefined : "No model cost recorded yet"}
            />
            <StatCard
              label="Avg. duration"
              value={formatDuration(stats.avg_duration_ms)}
              icon={Clock}
            />
          </div>

          {/* Daily trend chart */}
          <div className="rounded-xl bg-card p-4 ring-1 ring-foreground/10">
            <SectionHeader
              title="Daily activity"
              description="Agent invocations per day, last 7 days"
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
            <h2 className="border-b px-4 py-3 text-sm font-semibold">Usage by agent</h2>
            {stats.per_agent.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                No agent usage recorded yet.
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="pl-4">Agent</TableHead>
                    <TableHead className="text-right">Invocations</TableHead>
                    <TableHead className="text-right">Tokens in</TableHead>
                    <TableHead className="text-right">Tokens out</TableHead>
                    <TableHead className="text-right">Est. cost</TableHead>
                    <TableHead className="text-right">Avg. duration</TableHead>
                    <TableHead className="w-[120px] pr-4">Volume</TableHead>
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
                        <TableCell className="pl-4 font-medium">
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
                        <TableCell className="pr-4">
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

          {/* Per-day totals: with a live model this is where token spend shows up day by day. */}
          <div className="rounded-xl bg-card ring-1 ring-foreground/10">
            <h2 className="border-b px-4 py-3 text-sm font-semibold">Daily totals (last 7 days)</h2>
            {stats.daily_trend.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                No usage recorded in the last 7 days.
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="pl-4">Date</TableHead>
                    <TableHead className="text-right">Invocations</TableHead>
                    <TableHead className="text-right">Tokens in</TableHead>
                    <TableHead className="text-right">Tokens out</TableHead>
                    <TableHead className="pr-4 text-right">Total tokens</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {stats.daily_trend.map((day) => (
                    <TableRow key={day.date}>
                      <TableCell className="pl-4 font-medium">{formatDate(day.date)}</TableCell>
                      <TableCell className="text-right">{formatNumber(day.invocations)}</TableCell>
                      <TableCell className="text-right text-muted-foreground">{formatNumber(day.tokens_in)}</TableCell>
                      <TableCell className="text-right text-muted-foreground">{formatNumber(day.tokens_out)}</TableCell>
                      <TableCell className="pr-4 text-right text-muted-foreground">
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
    </PageContainer>
  );
}
