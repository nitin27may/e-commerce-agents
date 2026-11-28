"use client";

import { Download, ExternalLink, RotateCcw } from "lucide-react";
import { StatusBadge, TONE_TEXT_CLASS, type SemanticTone } from "@/components/ui/status-badge";
import { apiUrl } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import type { ReturnData } from "@/lib/chat-schemas";
import { CardAction, ChatCard } from "./chat-card";
import { humanizeEnum } from "./card-format";

const STATUS: Array<{ match: RegExp; title: string; tone: SemanticTone }> = [
  { match: /pending|await|review/, title: "Return awaiting review", tone: "warning" },
  { match: /reject|denied|declin/, title: "Return declined", tone: "destructive" },
  { match: /complete|refunded|received|closed/, title: "Return complete", tone: "success" },
  { match: /approved|initiated|requested|label|created|open/, title: "Return started", tone: "info" },
];

export function returnStatus(status: string | undefined): { title: string; tone: SemanticTone } {
  const s = (status ?? "").toLowerCase();
  return STATUS.find((entry) => entry.match.test(s)) ?? { title: "Return", tone: "neutral" };
}

export function ChatReturnCard({ data }: { data: ReturnData }) {
  const { title, tone } = returnStatus(data.status);

  const footer = (data.return_label_url || data.order_id) && (
    <>
      {data.return_label_url && (
        <CardAction icon={Download} href={apiUrl(data.return_label_url)} external>
          Download return label
        </CardAction>
      )}
      {data.order_id && (
        <CardAction icon={ExternalLink} href={`/orders/${data.order_id}`} external>
          View order
        </CardAction>
      )}
    </>
  );

  const rows: Array<[string, React.ReactNode]> = [];
  if (data.refund_amount != null) {
    rows.push(["Refund", <span key="amount" className={`font-medium tabular-nums ${TONE_TEXT_CLASS.success}`}>{formatPrice(data.refund_amount)}</span>]);
  }
  if (data.refund_method) rows.push(["Refund to", humanizeEnum(data.refund_method)]);
  if (data.refund_timeline) rows.push(["Expected", data.refund_timeline]);

  return (
    <ChatCard
      icon={RotateCcw}
      title={title}
      meta={data.status && <StatusBadge label={humanizeEnum(data.status)} tone={tone} />}
      footer={footer || undefined}
    >
      {(rows.length > 0 || !!data.return_label_url) && (
        <>
          {rows.length > 0 && (
            <dl className="space-y-1.5 text-sm">
              {rows.map(([label, value]) => (
                <div key={label} className="flex items-center justify-between gap-4">
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="text-right text-foreground">{value}</dd>
                </div>
              ))}
            </dl>
          )}
          {data.return_label_url && (
            <p className="text-xs text-muted-foreground">
              Print the label, pack the items and drop the parcel at any carrier location.
            </p>
          )}
        </>
      )}
    </ChatCard>
  );
}
