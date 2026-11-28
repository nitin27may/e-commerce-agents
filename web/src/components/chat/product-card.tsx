"use client";

import { ExternalLink, GitCompare, MessageSquare, ShoppingBag } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { CHIP_CLASS, TONE_BADGE_CLASS } from "@/components/ui/status-badge";
import { StarRating } from "@/components/star-rating";
import { productImageUrl } from "@/lib/images";
import { useAuth } from "@/lib/auth-context";
import { formatPrice } from "@/lib/format";
import type { ProductData } from "@/lib/chat-schemas";
import { cn } from "@/lib/utils";
import { AddToCartAction, productHref, validProductId } from "./add-to-cart-action";
import { CardAction, ChatCard } from "./chat-card";
import { discountPercent } from "./card-format";

// Categories are labels, not status, so they use the chip mixes (text darkened to
// clear 4.5:1 on the tint) rather than the raw chart tokens, which did not.
const CATEGORY_CHIP: Record<string, string> = {
  electronics: CHIP_CLASS.primary,
  clothing: CHIP_CLASS.teal,
  home: CHIP_CLASS.slate,
  sports: TONE_BADGE_CLASS.success,
  books: TONE_BADGE_CLASS.warning,
};

export function categoryChipClass(category: string | undefined): string {
  return CATEGORY_CHIP[(category ?? "").toLowerCase()] ?? CHIP_CLASS.slate;
}

/** Price, struck-through original and one "Save N%" badge: the same treatment everywhere in the chat. */
export function ProductPrice({
  price,
  originalPrice,
  align = "start",
}: {
  price?: number;
  originalPrice?: number;
  align?: "start" | "center";
}) {
  if (price == null) return null;
  const pct = discountPercent(price, originalPrice);
  return (
    <div className={cn("flex flex-wrap items-baseline gap-x-1.5 gap-y-1", align === "center" && "justify-center")}>
      <span className="text-base font-semibold tabular-nums text-foreground">{formatPrice(price)}</span>
      {pct > 0 && (
        <>
          <span className="text-xs tabular-nums text-muted-foreground line-through">{formatPrice(originalPrice)}</span>
          <Badge variant="outline" className={cn("px-1.5 py-0 text-xs", TONE_BADGE_CLASS.success)}>
            Save {pct}%
          </Badge>
        </>
      )}
    </div>
  );
}

export function ProductRating({
  rating,
  reviewCount,
  align = "start",
}: {
  rating?: number;
  reviewCount?: number;
  align?: "start" | "center";
}) {
  if (rating == null) return null;
  return (
    <div className={cn("flex items-center gap-1.5", align === "center" && "justify-center")}>
      <StarRating rating={rating} size="xs" />
      <span className="text-xs tabular-nums text-muted-foreground">
        {rating.toFixed(1)}
        {reviewCount != null && ` (${reviewCount})`}
      </span>
    </div>
  );
}

interface ChatProductCardProps {
  data: ProductData;
  onAction?: (message: string) => void;
}

export function ChatProductCard({ data, onAction }: ChatProductCardProps) {
  const { isAuthenticated } = useAuth();
  if (!data.name) return null;

  const productId = validProductId(data.id);
  const name = data.name;

  const footer = (productId || onAction) && (
    <>
      {productId && <AddToCartAction productId={productId} productName={name} onAction={onAction} />}
      {productId && (
        <CardAction icon={ExternalLink} href={productHref(productId, isAuthenticated)} external>
          Details
        </CardAction>
      )}
      {onAction && (
        // Secondary actions go icon-only so the row fits a phone without clipping.
        <div className="ml-auto flex items-center gap-1">
          <CardAction
            icon={GitCompare}
            variant="ghost"
            iconOnly
            label={`Compare ${name} with similar products`}
            onClick={() => onAction(`Compare the ${name} with similar products`)}
          />
          <CardAction
            icon={MessageSquare}
            variant="ghost"
            iconOnly
            label={`Reviews for ${name}`}
            onClick={() => onAction(`What are the reviews like for the ${name}?`)}
          />
        </div>
      )}
    </>
  );

  return (
    <ChatCard label={name} footer={footer || undefined}>
      <div className="flex gap-3">
        <div className="flex size-20 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-muted">
          {productId ? (
            <img
              src={productImageUrl(productId, 160, 160, data.image_url, data.category)}
              alt={name}
              className="size-full object-cover"
              loading="lazy"
            />
          ) : (
            <ShoppingBag className="size-7 text-muted-foreground" aria-hidden />
          )}
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h4 className="line-clamp-2 text-sm font-semibold leading-tight text-foreground">{name}</h4>
          {(data.brand || data.category) && (
            <div className="flex flex-wrap items-center gap-1.5">
              {data.brand && <span className="text-xs text-muted-foreground">{data.brand}</span>}
              {data.category && (
                <Badge variant="outline" className={cn("px-1.5 py-0 text-xs", categoryChipClass(data.category))}>
                  {data.category}
                </Badge>
              )}
            </div>
          )}
          {data.description && (
            <p className="line-clamp-2 text-xs leading-snug text-muted-foreground">{data.description}</p>
          )}
          <div className="mt-auto space-y-0.5 pt-0.5">
            <ProductPrice price={data.price} originalPrice={data.original_price} />
            <ProductRating rating={data.rating} reviewCount={data.review_count} />
          </div>
        </div>
      </div>
    </ChatCard>
  );
}
