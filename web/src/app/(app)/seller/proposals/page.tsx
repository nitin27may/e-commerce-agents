"use client";

import { useCallback, useEffect, useState } from "react";
import { Lightbulb, Loader2, Sparkles } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { api, type ProposalLineDecision, type SellerProposal } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { ProposalCard } from "@/components/seller/proposal-card";

export default function SellerProposalsPage() {
  const { user, isLoading: authLoading, isAdmin } = useAuth();
  const isSeller = user?.role === "seller" || isAdmin;

  const [proposals, setProposals] = useState<SellerProposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      setProposals((await api.getSellerProposals()).proposals);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load proposals");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user && isSeller) load();
  }, [user, isSeller, load]);

  async function generate() {
    setGenerating(true);
    setNotice(null);
    try {
      const res = await api.runSellerProposals();
      setNotice(
        res.proposal_id
          ? `Created a proposal with ${res.lines} suggestion${res.lines === 1 ? "" : "s"}.`
          : "Nothing new to suggest right now.",
      );
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not generate suggestions");
    } finally {
      setGenerating(false);
    }
  }

  async function decide(id: string, lines: ProposalLineDecision[]) {
    const res = await api.decideSellerProposal(id, lines);
    const executed = res.results.filter((r) => r.status === "executed").length;
    const blocked = res.results.filter((r) => r.status === "blocked").length;
    setNotice(`Applied ${executed} change${executed === 1 ? "" : "s"}${blocked ? `; ${blocked} blocked by a limit` : ""}.`);
    await load();
  }

  if (authLoading) return null;
  if (!user || !isSeller) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState icon={Lightbulb} title="Access Denied" description="Proposals are for sellers." />
      </div>
    );
  }

  const open = proposals.filter((p) => p.status === "pending_review");
  const past = proposals.filter((p) => p.status !== "pending_review");

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Proposals</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Price markdowns and restock orders suggested for your products. Nothing changes until you accept it.
          </p>
        </div>
        <Button onClick={generate} disabled={generating}>
          {generating ? <Loader2 className="animate-spin" data-icon="inline-start" /> : <Sparkles data-icon="inline-start" />}
          Generate suggestions
        </Button>
      </div>

      {notice && (
        <p role="status" className="rounded-lg border bg-muted/40 px-3 py-2 text-sm">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 className="size-6 animate-spin text-muted-foreground" aria-label="Loading" />
        </div>
      ) : proposals.length === 0 ? (
        <EmptyState
          icon={Lightbulb}
          title="No proposals yet"
          description="Generate suggestions to see markdowns for overstocked products and restocks for low stock."
        />
      ) : (
        <>
          {open.map((p) => (
            <ProposalCard key={p.id} proposal={p} onDecide={decide} />
          ))}
          {past.length > 0 && (
            <section aria-labelledby="past-proposals" className="space-y-4">
              <h2 id="past-proposals" className="text-lg font-medium">
                Earlier proposals
              </h2>
              {past.map((p) => (
                <ProposalCard key={p.id} proposal={p} onDecide={decide} />
              ))}
            </section>
          )}
        </>
      )}
    </div>
  );
}
