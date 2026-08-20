"use client";

import { MessageSquareText, ThumbsDown, ThumbsUp } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { DistributionChart } from "@/components/ui/distribution-chart";
import { TrendChart } from "@/components/ui/trend-chart";
import { StatusBadge, type SemanticTone } from "@/components/ui/status-badge";

interface MonthlyRatingPoint {
  month: string;
  average_rating: number;
  review_count?: number;
}

interface SentimentData {
  product_id?: string;
  product_name?: string;
  overall_sentiment?: "very_positive" | "positive" | "mixed" | "negative" | "very_negative";
  average_rating?: number;
  total_reviews?: number;
  rating_distribution?: Record<string, number>;
  pros?: string[];
  cons?: string[];
  trend?: "improving" | "declining" | "stable" | "insufficient_data";
  monthly_data?: MonthlyRatingPoint[];
  risk_level?: "high" | "medium" | "low";
  suspicious_count?: number;
}

const SENTIMENT_CONFIG: Record<NonNullable<SentimentData["overall_sentiment"]>, { label: string; tone: SemanticTone }> = {
  very_positive: { label: "Very Positive", tone: "success" },
  positive: { label: "Positive", tone: "success" },
  mixed: { label: "Mixed", tone: "warning" },
  negative: { label: "Negative", tone: "destructive" },
  very_negative: { label: "Very Negative", tone: "destructive" },
};

const RISK_CONFIG: Record<NonNullable<SentimentData["risk_level"]>, { label: string; tone: SemanticTone }> = {
  low: { label: "Low Risk", tone: "success" },
  medium: { label: "Medium Risk", tone: "warning" },
  high: { label: "High Risk", tone: "destructive" },
};

const TREND_CONFIG: Record<NonNullable<SentimentData["trend"]>, { label: string; tone: SemanticTone }> = {
  improving: { label: "Improving", tone: "success" },
  stable: { label: "Stable", tone: "info" },
  declining: { label: "Declining", tone: "destructive" },
  insufficient_data: { label: "Not enough data", tone: "neutral" },
};

export function ChatSentimentCard({ data }: { data: SentimentData }) {
  // Nothing to show — e.g. a tool call resolved no data and the model
  // still emitted an all-empty fence. Don't render a header with a
  // blank body underneath it. product_name alone still counts: it
  // identifies which product this is about, unlike pricing-card's fence
  // (no equivalent identifying field there).
  const hasAnyData =
    data.product_name != null ||
    data.overall_sentiment != null ||
    data.average_rating != null ||
    data.total_reviews != null ||
    (data.rating_distribution && Object.keys(data.rating_distribution).length > 0) ||
    (data.pros && data.pros.length > 0) ||
    (data.cons && data.cons.length > 0) ||
    data.trend != null ||
    (data.monthly_data && data.monthly_data.length > 0) ||
    data.risk_level != null ||
    data.suspicious_count != null;
  if (!hasAnyData) return null;

  const distributionData = data.rating_distribution
    ? [5, 4, 3, 2, 1]
        .filter((r) => data.rating_distribution![String(r)] != null)
        .map((r) => ({ label: `${r}★`, value: data.rating_distribution![String(r)] }))
    : [];

  return (
    <div className="my-2 max-w-md rounded-xl border border-border bg-card shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 border-b border-border bg-muted px-4 py-2.5">
        <div className="flex items-center gap-2 min-w-0">
          <MessageSquareText className="size-4 text-muted-foreground shrink-0" />
          <span className="text-sm font-medium text-foreground truncate">
            {data.product_name || "Review Sentiment"}
          </span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {data.overall_sentiment && (
            <StatusBadge {...SENTIMENT_CONFIG[data.overall_sentiment]} />
          )}
          {data.risk_level && <StatusBadge {...RISK_CONFIG[data.risk_level]} />}
        </div>
      </div>

      <div className="p-4 space-y-3">
        {/* Headline metric */}
        {(data.average_rating != null || data.total_reviews != null || data.trend) && (
          <div className="flex items-baseline gap-2">
            {data.average_rating != null && (
              <span className="text-2xl font-bold text-foreground">{data.average_rating.toFixed(1)}</span>
            )}
            {data.total_reviews != null && (
              <span className="text-xs text-muted-foreground">
                from {data.total_reviews} review{data.total_reviews !== 1 ? "s" : ""}
              </span>
            )}
            {data.trend && (
              <Badge variant="outline" className="ml-auto text-[10px] px-1.5 py-0">
                {TREND_CONFIG[data.trend].label}
              </Badge>
            )}
          </div>
        )}

        {/* Rating distribution */}
        {distributionData.length > 0 && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1">Rating distribution</p>
            <DistributionChart data={distributionData} height={140} valueLabel="Reviews" />
          </div>
        )}

        {/* Monthly trend */}
        {data.monthly_data && data.monthly_data.length > 0 && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1">Rating over time</p>
            <TrendChart
              data={data.monthly_data.map((m) => ({ month: m.month, average_rating: m.average_rating }))}
              xKey="month"
              series={[{ key: "average_rating", label: "Avg rating" }]}
              height={140}
            />
          </div>
        )}

        {/* Pros / cons */}
        {((data.pros && data.pros.length > 0) || (data.cons && data.cons.length > 0)) && (
          <div className="grid grid-cols-2 gap-3 text-xs">
            {data.pros && data.pros.length > 0 && (
              <div className="space-y-1">
                <p className="flex items-center gap-1 font-medium text-success">
                  <ThumbsUp className="size-3" /> Pros
                </p>
                <ul className="space-y-0.5 text-muted-foreground">
                  {data.pros.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              </div>
            )}
            {data.cons && data.cons.length > 0 && (
              <div className="space-y-1">
                <p className="flex items-center gap-1 font-medium text-destructive">
                  <ThumbsDown className="size-3" /> Cons
                </p>
                <ul className="space-y-0.5 text-muted-foreground">
                  {data.cons.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* Suspicious review count */}
        {data.suspicious_count != null && data.suspicious_count > 0 && (
          <p className="text-[11px] text-muted-foreground">
            {data.suspicious_count} review{data.suspicious_count !== 1 ? "s" : ""} flagged as potentially fake
          </p>
        )}
      </div>
    </div>
  );
}
