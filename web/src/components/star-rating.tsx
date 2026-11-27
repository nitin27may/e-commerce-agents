"use client";
import { Star } from "lucide-react";
import { cn } from "@/lib/utils";

const SIZE_CLASS = { xs: "size-3", sm: "size-3.5", md: "size-4", lg: "size-5" } as const;

/** Five stars with half-star precision, drawn in the warning token so they follow the theme. */
export function StarRating({
  rating,
  max = 5,
  size = "sm",
  className,
}: {
  rating: number;
  max?: number;
  size?: keyof typeof SIZE_CLASS;
  className?: string;
}) {
  return (
    <div role="img" aria-label={`Rated ${rating.toFixed(1)} out of ${max}`} className={cn("flex items-center gap-0.5", className)}>
      {Array.from({ length: max }, (_, i) => {
        const filled = i < Math.floor(rating);
        const half = !filled && i < rating;
        return (
          <Star
            key={i}
            aria-hidden
            className={cn(
              SIZE_CLASS[size],
              filled ? "fill-warning text-warning" : half ? "fill-warning/50 text-warning" : "fill-border text-border",
            )}
          />
        );
      })}
    </div>
  );
}
