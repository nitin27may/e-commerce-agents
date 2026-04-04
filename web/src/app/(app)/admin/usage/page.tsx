"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { Separator } from "@/components/ui/separator";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";
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
  ArrowDownToLine,
  ArrowUpFromLine,
  Clock,
  Loader2,
  ShieldAlert,
  BarChart3,
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

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function formatDuration(ms: number): string {
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

// ---------------------------------------------------------------------------
// Simple inline bar for visual weight
// ---------------------------------------------------------------------------

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
    <div className="h-1.5 w-full rounded-full bg-slate-100">
      <div
        className={`h-1.5 rounded-full transition-all ${className ?? "bg-teal-500"}`}
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
      const data = await api.getUsageStats();
      setStats(data);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load usage stats"
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
      <div className="flex min-h-screen items-center justify-center bg-slate-50">
        <div className="text-center">
          <div className="mx-auto flex size-16 items-center justify-center rounded-full bg-red-50">
            <ShieldAlert className="size-8 text-red-500" />
          </div>
          <h2 className="mt-4 text-lg font-semibold text-slate-900">
            Access Denied
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            You do not have admin privileges to view this page.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-teal-600">
              <BarChart3 className="size-5 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-slate-900">
                Usage Analytics
              </h1>
              <p className="text-sm text-slate-500">
                Detailed token and invocation metrics across all agents
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-6 animate-spin text-teal-600" />
            <span className="ml-2 text-sm text-slate-500">
              Loading usage data...
            </span>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {!loading && !error && stats && (
          <div className="space-y-8">
            {/* Summary cards */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-slate-500">
                    Total Invocations
                  </CardTitle>
                  <Activity className="size-4 text-teal-600" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-slate-900">
                    {formatNumber(stats.total_invocations)}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-slate-500">
                    Tokens In
                  </CardTitle>
                  <ArrowDownToLine className="size-4 text-sky-500" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-slate-900">
                    {formatNumber(stats.total_tokens_in)}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-slate-500">
                    Tokens Out
                  </CardTitle>
                  <ArrowUpFromLine className="size-4 text-amber-500" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-slate-900">
                    {formatNumber(stats.total_tokens_out)}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium text-slate-500">
                    Avg Duration
                  </CardTitle>
                  <Clock className="size-4 text-orange-500" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-slate-900">
                    {formatDuration(stats.avg_duration_ms)}
                  </div>
                </CardContent>
              </Card>
            </div>

            {/* Per-agent breakdown */}
            <div className="rounded-xl border border-slate-200 bg-white">
              <div className="px-4 py-3">
                <h2 className="text-sm font-semibold text-slate-700">
                  Per-Agent Breakdown
                </h2>
              </div>
              <Separator />
              {stats.per_agent.length === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-slate-400">
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
                      <TableHead className="text-right">Avg Duration</TableHead>
                      <TableHead className="w-[120px]">Volume</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(() => {
                      const maxInvocations = Math.max(
                        ...stats.per_agent.map((a) => a.invocations),
                        1
                      );
                      return stats.per_agent.map((agent) => (
                        <TableRow key={agent.agent_id}>
                          <TableCell className="font-medium text-slate-800">
                            {agent.agent_name}
                          </TableCell>
                          <TableCell className="text-right">
                            {formatNumber(agent.invocations)}
                          </TableCell>
                          <TableCell className="text-right text-slate-500">
                            {formatNumber(agent.tokens_in)}
                          </TableCell>
                          <TableCell className="text-right text-slate-500">
                            {formatNumber(agent.tokens_out)}
                          </TableCell>
                          <TableCell className="text-right text-slate-500">
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

            {/* Daily trend */}
            <div className="rounded-xl border border-slate-200 bg-white">
              <div className="px-4 py-3">
                <h2 className="text-sm font-semibold text-slate-700">
                  Daily Trend (Last 7 Days)
                </h2>
              </div>
              <Separator />
              {stats.daily_trend.length === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-slate-400">
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
                      <TableHead className="w-[120px]">Activity</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(() => {
                      const maxDayInvocations = Math.max(
                        ...stats.daily_trend.map((d) => d.invocations),
                        1
                      );
                      return stats.daily_trend.map((day) => (
                        <TableRow key={day.date}>
                          <TableCell className="font-medium text-slate-800">
                            {formatDate(day.date)}
                          </TableCell>
                          <TableCell className="text-right">
                            {formatNumber(day.invocations)}
                          </TableCell>
                          <TableCell className="text-right text-slate-500">
                            {formatNumber(day.tokens_in)}
                          </TableCell>
                          <TableCell className="text-right text-slate-500">
                            {formatNumber(day.tokens_out)}
                          </TableCell>
                          <TableCell className="text-right text-slate-500">
                            {formatNumber(day.tokens_in + day.tokens_out)}
                          </TableCell>
                          <TableCell>
                            <InlineBar
                              value={day.invocations}
                              max={maxDayInvocations}
                              className="bg-sky-500"
                            />
                          </TableCell>
                        </TableRow>
                      ));
                    })()}
                  </TableBody>
                </Table>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
