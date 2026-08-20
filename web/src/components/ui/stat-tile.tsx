import { StatCard, type StatCardProps } from "@/components/ui/stat-card";
import { TONE_TEXT_CLASS, type SemanticTone } from "@/components/ui/status-badge";
import { cn } from "@/lib/utils";

export interface StatTileProps extends StatCardProps {
  /** Colors the value text via the same tone tokens StatusBadge uses. */
  tone?: SemanticTone;
}

/**
 * Thin StatCard wrapper for generative-UI scalar callouts (e.g. "In Stock:
 * 42 units", "Risk Level: High") where the value itself carries a semantic
 * tone — StatCard alone has no notion of tone, so this layers it on rather
 * than duplicating the card chrome.
 */
export function StatTile({ tone, valueClassName, ...props }: StatTileProps) {
  return (
    <StatCard
      {...props}
      valueClassName={cn(tone && TONE_TEXT_CLASS[tone], valueClassName)}
    />
  );
}
