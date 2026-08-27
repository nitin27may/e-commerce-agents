"use client";

import { useState } from "react";
import { CheckIcon, XIcon, Loader2Icon, ShieldAlertIcon } from "lucide-react";
import { api } from "@/lib/api";

/**
 * The approve/reject control for a run that paused on a human, rendered
 * inline in the chat thread.
 *
 * `workflow:return-replace` gates on `ctx.request_info` and genuinely stops
 * there. Until now the only control that could release it lived on `/runs`,
 * so the user who caused the pause — sitting in chat, looking at a message
 * that just stopped mid-return — had no way to act on it without knowing
 * that a separate page existed and that their run was on it. A pause the
 * pauser cannot see is indistinguishable from a hang.
 *
 * Resolution is deliberately local state, not a refetch. The resumed text
 * comes straight back from the resume call, so the thread can show the
 * outcome immediately; re-reading the conversation would be a slower way to
 * learn what this component was already told.
 */

export type ApprovalOutcome = { approved: boolean; text: string; agentsInvolved: string[] };

export interface ApprovalCardProps {
  runId: string;
  /** Called with the resumed turn so the caller can append it to the thread. */
  onResolved: (outcome: ApprovalOutcome) => void;
}

export function ApprovalCard({ runId, onResolved }: ApprovalCardProps) {
  const [pending, setPending] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<boolean | null>(null);

  async function resolve(approved: boolean) {
    setPending(approved ? "approve" : "reject");
    setError(null);
    try {
      const res = await api.resumeRun(runId, approved);
      setDone(approved);
      onResolved({
        approved,
        text: res.text,
        agentsInvolved: res.agents_involved ?? [],
      });
    } catch (err) {
      // Surface the failure rather than silently reverting to two live
      // buttons: a second click on an approval that already went through is
      // the one mistake this control must not invite.
      setError(err instanceof Error ? err.message : "Could not submit that decision.");
    } finally {
      setPending(null);
    }
  }

  if (done !== null) {
    return (
      <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
        {done ? <CheckIcon className="size-3.5" /> : <XIcon className="size-3.5" />}
        {done ? "Approved" : "Rejected"} — the workflow resumed from its checkpoint.
      </div>
    );
  }

  return (
    <div className="mt-2.5 rounded-lg border border-amber-500/40 bg-amber-500/5 p-2.5">
      <div className="flex items-center gap-1.5 text-xs font-medium text-amber-700 dark:text-amber-400">
        <ShieldAlertIcon className="size-3.5 shrink-0" />
        This return needs your approval before it can continue
      </div>

      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={() => resolve(true)}
          disabled={pending !== null}
          className="inline-flex h-7 items-center gap-1 rounded-md bg-primary px-2.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
        >
          {pending === "approve" ? (
            <Loader2Icon className="size-3 animate-spin" />
          ) : (
            <CheckIcon className="size-3" />
          )}
          Approve
        </button>
        <button
          type="button"
          onClick={() => resolve(false)}
          disabled={pending !== null}
          className="inline-flex h-7 items-center gap-1 rounded-md border px-2.5 text-xs font-medium transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
        >
          {pending === "reject" ? (
            <Loader2Icon className="size-3 animate-spin" />
          ) : (
            <XIcon className="size-3" />
          )}
          Reject
        </button>
      </div>

      {error && <p className="mt-1.5 text-[11px] text-destructive">{error}</p>}
    </div>
  );
}
