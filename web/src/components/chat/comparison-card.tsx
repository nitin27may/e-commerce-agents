"use client";

import { ExternalLink, GitCompare } from "lucide-react";
import { StatusBadge } from "@/components/ui/status-badge";
import { productImageUrl } from "@/lib/images";
import { useAuth } from "@/lib/auth-context";
import { formatPrice } from "@/lib/format";
import type { CardData, ProductData } from "@/lib/chat-schemas";
import { cn } from "@/lib/utils";
import { AddToCartAction, productHref, validProductId } from "./add-to-cart-action";
import { CardAction, ChatCard } from "./chat-card";
import { ProductPrice, ProductRating } from "./product-card";

interface ComparisonCardProps {
  data: CardData<"comparison">;
  onAction?: (message: string) => void;
}

type Better = "first" | "second" | null;

function better(a: number | undefined, b: number | undefined, higherWins: boolean): Better {
  if (a == null || b == null || a === b) return null;
  return a > b === higherWins ? "first" : "second";
}

function ProductColumn({
  product,
  lowerPrice,
  higherRating,
  onAction,
}: {
  product: ProductData;
  lowerPrice: boolean;
  higherRating: boolean;
  onAction?: (message: string) => void;
}) {
  const { isAuthenticated } = useAuth();
  const productId = validProductId(product.id);

  return (
    <div className="flex min-w-0 flex-col items-center gap-2 px-4 py-4 text-center">
      <div className="size-24 overflow-hidden rounded-lg bg-muted">
        {productId && (
          <img
            src={productImageUrl(productId, 192, 192, product.image_url, product.category)}
            alt={product.name}
            className="size-full object-cover"
            loading="lazy"
          />
        )}
      </div>

      {/* Fixed height whether or not this product wins anything, so the two columns stay aligned. */}
      <div className="flex min-h-5 flex-wrap justify-center gap-1">
        {lowerPrice && <StatusBadge label="Lower price" tone="success" className="px-1.5 py-0 text-xs" />}
        {higherRating && <StatusBadge label="Higher rating" tone="info" className="px-1.5 py-0 text-xs" />}
      </div>

      <div className="min-w-0">
        <h4 className="line-clamp-2 text-sm font-semibold leading-snug text-foreground">{product.name}</h4>
        {product.brand && <p className="text-xs text-muted-foreground">{product.brand}</p>}
      </div>

      <ProductPrice price={product.price} originalPrice={product.original_price} align="center" />
      <ProductRating rating={product.rating} reviewCount={product.review_count} align="center" />

      {productId && (
        <div className="mt-auto flex w-full flex-col gap-1.5 pt-1">
          <AddToCartAction productId={productId} productName={product.name} onAction={onAction} className="w-full" />
          <CardAction icon={ExternalLink} href={productHref(productId, isAuthenticated)} external className="w-full">
            Details
          </CardAction>
        </div>
      )}
    </div>
  );
}

interface Row {
  label: string;
  first: string;
  second: string;
  winner: Better;
}

function comparisonRows(a: ProductData, b: ProductData): Row[] {
  const rows: Row[] = [];
  if (a.price != null || b.price != null) {
    rows.push({
      label: "Price",
      first: a.price != null ? formatPrice(a.price) : "—",
      second: b.price != null ? formatPrice(b.price) : "—",
      winner: better(a.price, b.price, false),
    });
  }
  if (a.rating != null || b.rating != null) {
    rows.push({
      label: "Rating",
      first: a.rating != null ? `${a.rating.toFixed(1)} / 5` : "—",
      second: b.rating != null ? `${b.rating.toFixed(1)} / 5` : "—",
      winner: better(a.rating, b.rating, true),
    });
  }
  if (a.review_count != null || b.review_count != null) {
    rows.push({
      label: "Reviews",
      first: a.review_count != null ? String(a.review_count) : "—",
      second: b.review_count != null ? String(b.review_count) : "—",
      winner: null,
    });
  }
  // Specs are not in the schema today, but pass through when a model sends them.
  const specsA = a.specs as Record<string, unknown> | undefined;
  const specsB = b.specs as Record<string, unknown> | undefined;
  if (specsA && specsB && typeof specsA === "object" && typeof specsB === "object") {
    for (const key of Object.keys(specsA).filter((k) => k in specsB).slice(0, 6)) {
      const label = key.replace(/_/g, " ");
      rows.push({
        label: label.charAt(0).toUpperCase() + label.slice(1),
        first: String(specsA[key]),
        second: String(specsB[key]),
        winner: null,
      });
    }
  }
  return rows;
}

export function ComparisonCard({ data, onAction }: ComparisonCardProps) {
  const [a, b] = data;
  if (!a.name || !b.name) return null;

  const priceWinner = better(a.price, b.price, false);
  const ratingWinner = better(a.rating, b.rating, true);
  const rows = comparisonRows(a, b);

  return (
    <ChatCard icon={GitCompare} title="Side-by-side comparison" flush>
      <div className="grid grid-cols-2 divide-x divide-border">
        <ProductColumn product={a} lowerPrice={priceWinner === "first"} higherRating={ratingWinner === "first"} onAction={onAction} />
        <ProductColumn product={b} lowerPrice={priceWinner === "second"} higherRating={ratingWinner === "second"} onAction={onAction} />
      </div>

      {rows.length > 0 && (
        <table className="w-full border-t border-border text-xs">
          <caption className="sr-only">
            {a.name} compared with {b.name}
          </caption>
          <thead className="bg-muted/40">
            <tr>
              <th scope="col" className="w-1/4 px-4 py-1.5 text-left font-medium text-muted-foreground">
                <span className="sr-only">Attribute</span>
              </th>
              <th scope="col" className="px-2 py-1.5 text-center font-medium text-foreground">
                <span className="line-clamp-1">{a.name}</span>
              </th>
              <th scope="col" className="px-2 py-1.5 text-center font-medium text-foreground">
                <span className="line-clamp-1">{b.name}</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.map((row) => (
              <tr key={row.label}>
                <th scope="row" className="px-4 py-1.5 text-left font-normal text-muted-foreground">
                  {row.label}
                </th>
                {(["first", "second"] as const).map((side) => (
                  <td
                    key={side}
                    className={cn(
                      "px-2 py-1.5 text-center tabular-nums",
                      row.winner === side ? "font-semibold text-foreground" : "text-muted-foreground",
                      row.winner === null && "text-foreground",
                    )}
                  >
                    {row[side]}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </ChatCard>
  );
}
