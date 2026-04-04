"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog";
import {
  Search,
  Package,
  Tag,
  BarChart3,
  Truck,
  Headphones,
  ShieldCheck,
  ArrowRight,
  Loader2,
  CheckCircle2,
  XCircle,
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

const CATEGORY_COLORS: Record<string, string> = {
  discovery: "bg-sky-50 text-sky-700 border-sky-200",
  orders: "bg-amber-50 text-amber-700 border-amber-200",
  pricing: "bg-emerald-50 text-emerald-700 border-emerald-200",
  analytics: "bg-indigo-50 text-indigo-700 border-indigo-200",
  logistics: "bg-orange-50 text-orange-700 border-orange-200",
  support: "bg-teal-50 text-teal-700 border-teal-200",
};

function getCategoryColor(category: string): string {
  const key = category.toLowerCase();
  for (const [k, v] of Object.entries(CATEGORY_COLORS)) {
    if (key.includes(k)) return v;
  }
  return "bg-slate-50 text-slate-700 border-slate-200";
}

function getAgentIcon(agentId: string | undefined | null): React.ElementType {
  if (!agentId) return Store;
  for (const [key, Icon] of Object.entries(AGENT_ICONS)) {
    if (agentId.includes(key)) return Icon;
  }
  return Store;
}

// ---------------------------------------------------------------------------
// Types from API
// ---------------------------------------------------------------------------

interface AgentCatalogEntry {
  id: string;
  name: string;
  display_name: string;
  description: string;
  category: string;
  capabilities: string[];
  requires_approval: boolean;
  status: string;
  user_has_access?: boolean;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function MarketplacePage() {
  const router = useRouter();
  const { user, isLoading: authLoading } = useAuth();

  const [agents, setAgents] = useState<AgentCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");

  // Request access dialog state
  const [selectedAgent, setSelectedAgent] = useState<AgentCatalogEntry | null>(
    null
  );
  const [useCase, setUseCase] = useState("");
  const [requesting, setRequesting] = useState(false);
  const [requestResult, setRequestResult] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
    }
  }, [user, authLoading, router]);

  const loadCatalog = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getAgentCatalog();
      setAgents(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load catalog");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user) loadCatalog();
  }, [user, loadCatalog]);

  async function handleRequestAccess() {
    if (!selectedAgent || !useCase.trim()) return;

    setRequesting(true);
    setRequestResult(null);
    try {
      await api.requestAccess(
        selectedAgent.name,
        "user",
        useCase.trim()
      );
      setRequestResult({
        type: "success",
        message: "Access request submitted successfully. An admin will review it shortly.",
      });
      setUseCase("");
      // Refresh catalog to update access status
      await loadCatalog();
    } catch (err) {
      setRequestResult({
        type: "error",
        message: err instanceof Error ? err.message : "Failed to submit request",
      });
    } finally {
      setRequesting(false);
    }
  }

  const filteredAgents = agents.filter((agent) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      agent.display_name.toLowerCase().includes(q) ||
      agent.description.toLowerCase().includes(q) ||
      agent.category.toLowerCase().includes(q) ||
      agent.capabilities.some((c) => c.toLowerCase().includes(q))
    );
  });

  if (authLoading || !user) return null;

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-teal-600">
              <Store className="size-5 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-slate-900">
                Agent Marketplace
              </h1>
              <p className="text-sm text-slate-500">
                Discover and request access to specialized AI agents
              </p>
            </div>
          </div>

          {/* Search */}
          <div className="mt-6 max-w-md">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
              <Input
                placeholder="Search agents by name, category, or capability..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9"
              />
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
              Loading agents...
            </span>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {!loading && !error && filteredAgents.length === 0 && (
          <div className="py-20 text-center">
            <Search className="mx-auto size-10 text-slate-300" />
            <p className="mt-3 text-sm text-slate-500">
              {searchQuery
                ? "No agents match your search."
                : "No agents available."}
            </p>
          </div>
        )}

        {/* Agent grid */}
        {!loading && !error && filteredAgents.length > 0 && (
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {filteredAgents.map((agent) => {
              const IconComponent = getAgentIcon(agent.name);
              const catColor = getCategoryColor(agent.category);

              return (
                <Card
                  key={agent.name}
                  className="flex flex-col transition-shadow hover:shadow-md"
                >
                  <CardHeader>
                    <div className="flex items-start justify-between">
                      <div className="flex items-center gap-3">
                        <div className="flex size-10 items-center justify-center rounded-lg bg-teal-50">
                          <IconComponent className="size-5 text-teal-600" />
                        </div>
                        <div>
                          <CardTitle className="text-base">
                            {agent.display_name}
                          </CardTitle>
                          <Badge
                            variant="outline"
                            className={`mt-1 text-[10px] font-normal ${catColor}`}
                          >
                            {agent.category}
                          </Badge>
                        </div>
                      </div>
                      {agent.status === "active" ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                          <span className="size-1.5 rounded-full bg-emerald-500" />
                          Active
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">
                          <span className="size-1.5 rounded-full bg-slate-400" />
                          Inactive
                        </span>
                      )}
                    </div>
                  </CardHeader>

                  <CardContent className="flex-1">
                    <CardDescription className="leading-relaxed">
                      {agent.description}
                    </CardDescription>

                    {/* Capabilities */}
                    <div className="mt-4 flex flex-wrap gap-1.5">
                      {agent.capabilities.map((cap) => (
                        <Badge
                          key={cap}
                          variant="secondary"
                          className="text-[10px] font-normal"
                        >
                          {cap}
                        </Badge>
                      ))}
                    </div>
                  </CardContent>

                  <CardFooter className="flex items-center justify-between">
                    {agent.user_has_access ? (
                      <div className="flex items-center gap-1.5 text-sm text-emerald-600">
                        <CheckCircle2 className="size-4" />
                        <span className="font-medium">Approved</span>
                      </div>
                    ) : !agent.requires_approval ? (
                      <Badge
                        variant="outline"
                        className="border-teal-200 bg-teal-50 text-teal-700"
                      >
                        Available
                      </Badge>
                    ) : (
                      <Badge
                        variant="outline"
                        className="border-amber-200 bg-amber-50 text-amber-700"
                      >
                        <ShieldCheck className="mr-1 size-3" />
                        Requires Approval
                      </Badge>
                    )}

                    {!agent.user_has_access && agent.requires_approval && (
                      <Dialog
                        onOpenChange={(open) => {
                          if (open) {
                            setSelectedAgent(agent);
                            setUseCase("");
                            setRequestResult(null);
                          } else {
                            setSelectedAgent(null);
                          }
                        }}
                      >
                        <DialogTrigger
                          render={
                            <Button
                              variant="default"
                              size="sm"
                              className="bg-teal-600 text-white hover:bg-teal-700"
                            />
                          }
                        >
                          Request Access
                          <ArrowRight className="ml-1 size-3" />
                        </DialogTrigger>

                        <DialogContent className="sm:max-w-md">
                          <DialogHeader>
                            <DialogTitle>
                              Request Access to {agent.display_name}
                            </DialogTitle>
                            <DialogDescription>
                              Describe your use case for this agent. An admin
                              will review your request.
                            </DialogDescription>
                          </DialogHeader>

                          <div className="space-y-3 py-2">
                            <div className="space-y-1.5">
                              <Label htmlFor="use-case">Use Case</Label>
                              <Textarea
                                id="use-case"
                                placeholder="Describe how you plan to use this agent..."
                                value={useCase}
                                onChange={(e) => setUseCase(e.target.value)}
                                className="min-h-24"
                              />
                            </div>

                            {requestResult && (
                              <div
                                className={`flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm ${
                                  requestResult.type === "success"
                                    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                                    : "border-red-200 bg-red-50 text-red-700"
                                }`}
                              >
                                {requestResult.type === "success" ? (
                                  <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
                                ) : (
                                  <XCircle className="mt-0.5 size-4 shrink-0" />
                                )}
                                {requestResult.message}
                              </div>
                            )}
                          </div>

                          <DialogFooter>
                            <DialogClose
                              render={<Button variant="outline" />}
                            >
                              Cancel
                            </DialogClose>
                            <Button
                              onClick={handleRequestAccess}
                              disabled={!useCase.trim() || requesting}
                              className="bg-teal-600 text-white hover:bg-teal-700"
                            >
                              {requesting && (
                                <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                              )}
                              Submit Request
                            </Button>
                          </DialogFooter>
                        </DialogContent>
                      </Dialog>
                    )}
                  </CardFooter>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
