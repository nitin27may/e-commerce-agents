"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  Search,
  Package,
  Tag,
  BarChart3,
  Truck,
  Headphones,
  Loader2,
  KeyRound,
  Store,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Agent icon map
// ---------------------------------------------------------------------------

const AGENT_ICONS: Record<string, React.ElementType> = {
  product_discovery: Search,
  order_management: Package,
  pricing_promotions: Tag,
  review_sentiment: BarChart3,
  inventory_fulfillment: Truck,
  customer_support: Headphones,
};

function getAgentIcon(agentId: string): React.ElementType {
  for (const [key, Icon] of Object.entries(AGENT_ICONS)) {
    if (agentId.includes(key)) return Icon;
  }
  return Store;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface MyAgent {
  agent_id: string;
  display_name: string;
  role: string;
  granted_at: string;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function MyAgentsPage() {
  const router = useRouter();
  const { user, isLoading: authLoading } = useAuth();

  const [agents, setAgents] = useState<MyAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
    }
  }, [user, authLoading, router]);

  const loadMyAgents = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getMyAgents();
      setAgents(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load agents");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user) loadMyAgents();
  }, [user, loadMyAgents]);

  function formatDate(dateStr: string): string {
    try {
      return new Date(dateStr).toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      return dateStr;
    }
  }

  if (authLoading || !user) return null;

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-teal-600">
              <KeyRound className="size-5 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-slate-900">My Agents</h1>
              <p className="text-sm text-slate-500">
                Agents you have been granted access to
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-6 animate-spin text-teal-600" />
            <span className="ml-2 text-sm text-slate-500">
              Loading your agents...
            </span>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {!loading && !error && agents.length === 0 && (
          <div className="py-20 text-center">
            <KeyRound className="mx-auto size-10 text-slate-300" />
            <p className="mt-3 text-sm font-medium text-slate-600">
              No agents yet
            </p>
            <p className="mt-1 text-sm text-slate-400">
              Visit the marketplace to request access to available agents.
            </p>
          </div>
        )}

        {!loading && !error && agents.length > 0 && (
          <div className="rounded-xl border border-slate-200 bg-white">
            <div className="px-4 py-3">
              <span className="text-sm font-medium text-slate-700">
                {agents.length} agent{agents.length !== 1 ? "s" : ""} available
              </span>
            </div>
            <Separator />
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-[40%]">Agent</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Granted</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {agents.map((agent) => {
                  const IconComponent = getAgentIcon(agent.agent_id);
                  return (
                    <TableRow key={agent.agent_id}>
                      <TableCell>
                        <div className="flex items-center gap-3">
                          <div className="flex size-8 items-center justify-center rounded-md bg-teal-50">
                            <IconComponent className="size-4 text-teal-600" />
                          </div>
                          <span className="font-medium text-slate-800">
                            {agent.display_name}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant="secondary"
                          className="font-normal text-xs"
                        >
                          {agent.role}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-slate-500">
                        {formatDate(agent.granted_at)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  );
}
