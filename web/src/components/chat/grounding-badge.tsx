"use client";

import { useState } from "react";
import { ChevronRight, ShieldCheck, ShieldAlert } from "lucide-react";
import type { GroundingClaim, GroundingReport } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<GroundingClaim["status"], string> = {
  verified: "verified",
  price_mismatch: "price corrected",
  not_found: "not found — stripped",
  unverifiable: "unverifiable",
};

const STATUS_CLASS: Record<GroundingClaim["status"], string> = {
  verified: "text-emerald-600 dark:text-emerald-400",
  price_mismatch: "text-amber-600 dark:text-amber-400",
  not_found: "text-destructive",
  unverifiable: "text-muted-foreground",
};

/**
 * "N facts verified against the database" badge — the per-message readout of
 * GroundingVerificationMiddleware's report (shared/grounding/middleware.py).
 * Collapsed summary always shown when there's at least one claim; expand to
 * see exactly which id/price/tracking claim was checked and against what.
 */
export function GroundingBadge({ report }: { report: GroundingReport | undefined | null }) {
  const [open, setOpen] = useState(false);

  if (!report || report.total === 0) return null;

  const allVerified = report.unverified === 0;

  return (
    <div className="mt-2 max-w-xl rounded-lg border bg-card/60 text-xs">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />
        {allVerified ? (
          <ShieldCheck className="size-3.5 text-emerald-500" />
        ) : (
          <ShieldAlert className="size-3.5 text-amber-500" />
        )}
        <span>
          {report.verified} fact{report.verified === 1 ? "" : "s"} verified against the database
          {report.unverified > 0 && `, ${report.unverified} unverified`}
        </span>
      </button>

      {open && (
        <ul className="divide-y border-t">
          {report.claims.map((claim, i) => (
            <ClaimRow key={`${claim.type}-${claim.id}-${i}`} claim={claim} />
          ))}
        </ul>
      )}
    </div>
  );
}

function ClaimRow({ claim }: { claim: GroundingClaim }) {
  return (
    <li className="flex items-start gap-2 px-2.5 py-1.5">
      <span className="rounded bg-primary/10 px-1.5 py-0.5 font-medium text-primary">{claim.type}</span>
      <div className="min-w-0 flex-1">
        <span className="truncate font-mono text-foreground/80">{claim.id}</span>
        <span className={cn("ml-2", STATUS_CLASS[claim.status])}>{STATUS_LABEL[claim.status]}</span>
        {claim.detail && <p className="mt-0.5 text-muted-foreground/80">{claim.detail}</p>}
      </div>
      {claim.source && (
        <span className="shrink-0 text-muted-foreground/60">{claim.source}</span>
      )}
    </li>
  );
}
