"use client";

import { useState } from "react";
import { CheckIcon, XIcon, Loader2Icon, ShieldAlertIcon } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { TONE_TEXT_CLASS } from "@/components/ui/status-badge";
import { CardAction } from "./chat-card";

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
 * Only staff can decide (#92): whoever asked for the return must not be the one
 * who approves it, and the backend refuses a non-admin resume with a 403. So a
 * customer sees a read-only "awaiting staff review" state here instead of
 * buttons that could only fail.
 *
 * Why this gates on `isAdmin` and not `isStaff`: this card only renders in the
 * conversation's owner's own thread (`chat/page.tsx`), so the viewer is the
 * requester. The approvals service lets `support` or `admin` decide but never
 * the requester, so a support user could not decide from here anyway. Staff
 * review other people's runs on `/runs` and in the approvals inbox, which is
 * where `isStaff` and the requester check live. The admin branch below is the
 * demo and single-user case; an admin deciding their own request is still
 * refused by the backend and the error is shown.
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
  const { isAdmin } = useAuth();
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
        {done ? "Approved. Continuing." : "Rejected."}
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div role="alert" className="mt-2.5 rounded-lg border border-warning/40 bg-warning/5 p-2.5">
        <div className={`flex items-center gap-1.5 text-xs font-medium ${TONE_TEXT_CLASS.warning}`}>
          <ShieldAlertIcon className="size-3.5 shrink-0" />
          Awaiting staff review
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          This return is above the auto-approval limit. A member of our staff will review it before it continues.
        </p>
      </div>
    );
  }

  return (
    <div role="alert" className="mt-2.5 rounded-lg border border-warning/40 bg-warning/5 p-2.5">
      <div className={`flex items-center gap-1.5 text-xs font-medium ${TONE_TEXT_CLASS.warning}`}>
        <ShieldAlertIcon className="size-3.5 shrink-0" />
        This return needs your approval before it can continue
      </div>

      <div className="mt-2 flex items-center gap-2">
        <CardAction
          variant="primary"
          icon={pending === "approve" ? Loader2Icon : CheckIcon}
          onClick={() => resolve(true)}
          disabled={pending !== null}
          className={pending === "approve" ? "[&_svg]:animate-spin" : undefined}
        >
          Approve
        </CardAction>
        <CardAction
          icon={pending === "reject" ? Loader2Icon : XIcon}
          onClick={() => resolve(false)}
          disabled={pending !== null}
          className={pending === "reject" ? "[&_svg]:animate-spin" : undefined}
        >
          Reject
        </CardAction>
      </div>

      {error && <p role="alert" className="mt-1.5 text-xs text-destructive">{error}</p>}
    </div>
  );
}
