import type { ApprovalDetail } from "@/lib/api";
import { formatPrice, shortOrderId } from "@/lib/format";
import { humanizeEnum } from "@/components/chat/card-format";

type Facts = Record<string, unknown>;

/** How a policy fact reads to a reviewer. Unknown facts fall back to a humanized key and the raw value. */
const FACT_LABELS: Record<string, { label: string; format?: (v: unknown) => string }> = {
  amount: { label: "Amount", format: (v) => formatPrice(Number(v)) },
  days_since_delivery: { label: "Days since delivery", format: (v) => String(Math.round(Number(v))) },
  returns_90d: { label: "Returns in 90 days", format: (v) => String(Math.round(Number(v))) },
  account_age_days: { label: "Account age (days)", format: (v) => String(Math.round(Number(v))) },
  loyalty_tier: { label: "Loyalty tier", format: (v) => humanizeEnum(String(v)) },
  refund_to_spend_ratio: { label: "Refund to lifetime spend", format: (v) => `${Math.round(Number(v) * 100)}%` },
};

function factRows(facts: Facts): [string, string][] {
  return Object.entries(facts)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([key, value]) => {
      const known = FACT_LABELS[key];
      return [known?.label ?? humanizeEnum(key), known?.format ? known.format(value) : String(value)];
    });
}

function requestRows(input: Facts): [string, string][] {
  return Object.entries(input)
    .filter(([, v]) => v !== null && v !== undefined && v !== "" && typeof v !== "object")
    .map(([key, value]) => {
      if (key === "order_id") return ["Order", `#${shortOrderId(String(value))}`];
      if (key === "refund_method") return ["Refund method", humanizeEnum(String(value))];
      return [humanizeEnum(key), String(value)];
    });
}

function Rows({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="text-foreground">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * The reviewer's view of an approval: why policy routed it here, what was asked for, and the facts the policy
 * weighed. The raw record stays one click further down for debugging; leading with it buried the reason.
 */
export function ApprovalDetailView({ detail }: { detail: ApprovalDetail }) {
  const recommendation = (detail.recommendation ?? {}) as {
    reasons?: string[];
    evidence?: { facts?: Facts };
  };
  const reasons = recommendation.reasons ?? [];
  const facts = factRows(recommendation.evidence?.facts ?? {});
  const source = (detail.source_detail ?? {}) as { tool_input?: Facts };
  const request = requestRows(source.tool_input ?? {});

  return (
    <div className="mt-2 space-y-3 rounded-lg border bg-muted/30 p-3">
      {reasons.length > 0 && (
        <section>
          <h4 className="mb-1 text-xs font-medium">Why it needs review</h4>
          <ul className="list-disc space-y-0.5 pl-4 text-xs text-foreground">
            {reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </section>
      )}
      {request.length > 0 && (
        <section>
          <h4 className="mb-1 text-xs font-medium">Requested</h4>
          <Rows rows={request} />
        </section>
      )}
      {facts.length > 0 && (
        <section>
          <h4 className="mb-1 text-xs font-medium">What the policy checked</h4>
          <Rows rows={facts} />
        </section>
      )}
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">Raw record</summary>
        <pre className="mt-1.5 max-h-48 overflow-auto rounded bg-muted/60 px-2 py-1.5 text-xs leading-relaxed text-foreground/70">
          {JSON.stringify(detail.source_detail ?? {}, null, 2)}
        </pre>
      </details>
    </div>
  );
}
