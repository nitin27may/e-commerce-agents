"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { formatDuration, formatNumber } from "@/lib/format";
import { StatCard } from "@/components/ui/stat-card";
import { PageContainer, PageHeader } from "@/components/ui/page-header";
import { AccessDenied } from "@/components/ui/access-denied";
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
  Zap,
  Bot,
  Clock,
  Loader2,
  Shield,
} from "lucide-react";

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
  active_agents: number;
  pending_requests: number;
  avg_duration_ms: number;
  per_agent: AgentUsage[];
  daily_trend: DailyTrend[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDay(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminDashboardPage() {
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
      // Map API response to frontend UsageStats shape
      const overall = raw.overall;
      setStats({
        total_invocations: overall.total_requests ?? 0,
        total_tokens_in: overall.total_tokens_in ?? 0,
        total_tokens_out: overall.total_tokens_out ?? 0,
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
        })),
        daily_trend: raw.daily_trend.map((d) => ({
          date: d.day,
          invocations: d.request_count,
          tokens_in: 0,
          tokens_out: 0,
        })),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load stats");
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
        icon={Shield}
        title="Admin overview"
        description="Platform activity and agent usage"
      />

        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-6 animate-spin text-primary" />
            <span className="ml-2 text-sm text-muted-foreground">
              Loading dashboard...
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
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard label="Invocations" value={formatNumber(stats.total_invocations)} icon={Activity} />
              <StatCard
                label="Tokens"
                value={formatNumber(stats.total_tokens_in + stats.total_tokens_out)}
                icon={Zap}
                hint={`${formatNumber(stats.total_tokens_in)} in / ${formatNumber(stats.total_tokens_out)} out`}
              />
              <StatCard label="Active agents" value={stats.active_agents} icon={Bot} />
              <StatCard label="Pending requests" value={stats.pending_requests} icon={Clock} />
            </div>

            <div className="grid gap-6 lg:grid-cols-3">
              <section className="rounded-xl bg-card ring-1 ring-foreground/10 lg:col-span-2" aria-labelledby="per-agent-usage">
                <h2 id="per-agent-usage" className="border-b px-4 py-3 text-sm font-semibold text-foreground">
                  Usage by agent
                </h2>
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
                        <TableHead className="pr-4 text-right">Avg. duration</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {stats.per_agent.map((agent) => (
                        <TableRow key={agent.agent_id}>
                          <TableCell className="pl-4 font-medium text-foreground">
                            {agent.agent_name}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {formatNumber(agent.invocations)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums text-muted-foreground">
                            {formatNumber(agent.tokens_in)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums text-muted-foreground">
                            {formatNumber(agent.tokens_out)}
                          </TableCell>
                          <TableCell className="pr-4 text-right tabular-nums text-muted-foreground">
                            {formatDuration(agent.avg_duration_ms)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </section>

              <section className="rounded-xl bg-card ring-1 ring-foreground/10" aria-labelledby="daily-trend">
                <h2 id="daily-trend" className="border-b px-4 py-3 text-sm font-semibold text-foreground">
                  Last 7 days
                </h2>
                {stats.daily_trend.length === 0 ? (
                  <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                    No activity in the last 7 days.
                  </div>
                ) : (
                  <ul className="divide-y divide-border">
                    {stats.daily_trend.map((day) => (
                      <li key={day.date} className="flex items-center justify-between px-4 py-2.5 text-sm">
                        <span className="text-muted-foreground">{formatDay(day.date)}</span>
                        <span className="tabular-nums">
                          <span className="font-medium text-foreground">{formatNumber(day.invocations)}</span>{" "}
                          <span className="text-muted-foreground">
                            {day.invocations === 1 ? "call" : "calls"}
                          </span>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
          </div>
        )}
    </PageContainer>
  );
}
