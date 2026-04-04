"use client";
import { Star } from "lucide-react";

export function StarRating({ rating, max = 5, size = "sm" }: { rating: number; max?: number; size?: "sm" | "md" | "lg" }) {
  const sizeClass = size === "sm" ? "size-3.5" : size === "md" ? "size-4" : "size-5";
  return (
    <div className="flex items-center gap-0.5">
      {Array.from({ length: max }, (_, i) => {
        const filled = i < Math.floor(rating);
        const half = !filled && i < rating;
        return (
          <Star key={i} className={`${sizeClass} ${filled ? "fill-amber-400 text-amber-400" : half ? "fill-amber-400/50 text-amber-400" : "fill-slate-200 text-slate-200"}`} />
        );
      })}
    </div>
  );
}
