"use client";

import { MessageSquareText, ShieldAlert, ShieldCheck, ThumbsDown, ThumbsUp } from "lucide-react";
import { TrendChart } from "@/components/ui/trend-chart";
import { StatusBadge, TONE_TEXT_CLASS, type SemanticTone } from "@/components/ui/status-badge";
import { StarRating } from "@/components/star-rating";
import type { SentimentData } from "@/lib/chat-schemas";
import { cn } from "@/lib/utils";
import { CardSection, ChatCard } from "./chat-card";
import { monthLabels } from "./card-format";

type Sentiment = NonNullable<SentimentData["overall_sentiment"]>;
type Risk = NonNullable<SentimentData["risk_level"]>;
type Trend = NonNullable<SentimentData["trend"]>;

const SENTIMENT: Record<Sentiment, { label: string; tone: SemanticTone }> = {
  very_positive: { label: "Very positive", tone: "success" },
  positive: { label: "Positive", tone: "success" },
  mixed: { label: "Mixed", tone: "warning" },
  negative: { label: "Negative", tone: "destructive" },
  very_negative: { label: "Very negative", tone: "destructive" },
};

const RISK: Record<Risk, { label: string; tone: SemanticTone }> = {
  low: { label: "Reviews look genuine", tone: "success" },
  medium: { label: "Some reviews look suspicious", tone: "warning" },
  high: { label: "Many reviews look suspicious", tone: "destructive" },
};

const TREND: Record<Trend, { label: string; tone: SemanticTone } | null> = {
  improving: { label: "Improving", tone: "success" },
  stable: { label: "Stable", tone: "info" },
  declining: { label: "Declining", tone: "warning" },
  insufficient_data: null,
};

/**
 * Pros and cons worth a line: phrases, not the single words ("Great", "Fast")
 * keyword extraction returns, which tell a shopper nothing.
 */
export function meaningfulPoints(points: string[] | undefined): string[] {
  return (points ?? []).filter((p) => p.trim().split(/\s+/).length >= 2);
}

function RatingBars({ distribution }: { distribution: Record<string, number> }) {
  const rows = [5, 4, 3, 2, 1].map((star) => ({ star, count: distribution[String(star)] ?? 0 }));
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <ul className="space-y-1" aria-label="Rating distribution">
      {rows.map(({ star, count }) => (
        <li key={star} className="flex items-center gap-2 text-xs">
          <span className="w-10 shrink-0 tabular-nums text-muted-foreground">{star} star</span>
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted" aria-hidden>
            <span className="block h-full rounded-full bg-warning" style={{ width: `${(count / max) * 100}%` }} />
          </span>
          <span className="w-6 shrink-0 text-right tabular-nums text-muted-foreground">{count}</span>
        </li>
      ))}
    </ul>
  );
}

function PointList({ title, points, tone, icon: Icon }: { title: string; points: string[]; tone: SemanticTone; icon: typeof ThumbsUp }) {
  return (
    <div className="space-y-1">
      <p className={cn("flex items-center gap-1 text-xs font-medium", TONE_TEXT_CLASS[tone])}>
        <Icon className="size-3" aria-hidden /> {title}
      </p>
      <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground marker:text-border">
        {points.map((p) => (
          <li key={p}>{p}</li>
        ))}
      </ul>
    </div>
  );
}

export function ChatSentimentCard({ data }: { data: SentimentData }) {
  const pros = meaningfulPoints(data.pros);
  const cons = meaningfulPoints(data.cons);
  const trend = data.trend ? TREND[data.trend] : null;
  const months = data.monthly_data ?? [];
  const labels = monthLabels(months.map((m) => m.month));
  const distribution = data.rating_distribution;
  const hasDistribution = !!distribution && Object.values(distribution).some((n) => n > 0);
  const risk = data.risk_level ? RISK[data.risk_level] : null;

  return (
    <ChatCard
      icon={MessageSquareText}
      title={data.product_name || "Review summary"}
      meta={data.overall_sentiment && <StatusBadge {...SENTIMENT[data.overall_sentiment]} />}
    >
      {(data.average_rating != null || data.total_reviews != null || trend) && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {data.average_rating != null && (
            <>
              <span className="text-2xl font-semibold tabular-nums text-foreground">{data.average_rating.toFixed(1)}</span>
              <StarRating rating={data.average_rating} size="sm" />
            </>
          )}
          {data.total_reviews != null && (
            <span className="text-xs text-muted-foreground">
              from {data.total_reviews} review{data.total_reviews !== 1 ? "s" : ""}
            </span>
          )}
          {trend && <StatusBadge {...trend} className="ml-auto" />}
        </div>
      )}

      {hasDistribution && (
        <CardSection title="Rating distribution">
          <RatingBars distribution={distribution!} />
        </CardSection>
      )}

      {months.length > 1 && (
        <CardSection title="Average rating by month">
          <TrendChart
            data={months.map((m, i) => ({ month: labels[i], average_rating: m.average_rating }))}
            xKey="month"
            series={[{ key: "average_rating", label: "Average rating" }]}
            height={140}
            yDomain={[0, 5]}
            yTicks={[1, 2, 3, 4, 5]}
          />
        </CardSection>
      )}

      {(pros.length > 0 || cons.length > 0) && (
        <div className={cn("grid gap-3", pros.length > 0 && cons.length > 0 && "sm:grid-cols-2")}>
          {pros.length > 0 && <PointList title="Pros" points={pros} tone="success" icon={ThumbsUp} />}
          {cons.length > 0 && <PointList title="Cons" points={cons} tone="destructive" icon={ThumbsDown} />}
        </div>
      )}

      {(risk || (data.suspicious_count ?? 0) > 0) && (
        <p className={cn("flex items-center gap-1.5 text-xs", TONE_TEXT_CLASS[risk?.tone ?? "warning"])}>
          {risk?.tone === "success" ? <ShieldCheck className="size-3.5" aria-hidden /> : <ShieldAlert className="size-3.5" aria-hidden />}
          <span>
            {risk?.label ?? "Some reviews look suspicious"}
            {(data.suspicious_count ?? 0) > 0 &&
              ` (${data.suspicious_count} review${data.suspicious_count !== 1 ? "s" : ""} flagged as potentially fake)`}
          </span>
        </p>
      )}
    </ChatCard>
  );
}
