"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Sparkles, Star, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatPrice } from "@/lib/format";
import { productImageUrl } from "@/lib/images";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

interface ProductDetail {
  id: string;
  name: string;
  brand?: string | null;
  category?: string | null;
  description?: string | null;
  price: number;
  original_price?: number | null;
  image_url?: string | null;
  rating?: number | null;
  review_count?: number | null;
  in_stock?: boolean;
}

export default function ProductDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { user } = useAuth();

  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    api
      .getProduct(id)
      .then((p) => setProduct(p as ProductDetail))
      .catch(() => setNotFound(true));
  }, [id]);

  async function addToCart() {
    if (!user) {
      router.push("/login");
      return;
    }
    setAdding(true);
    try {
      await api.addToCart(id, 1);
      router.push("/cart");
    } catch {
      setAdding(false);
    }
  }

  if (notFound) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-16 text-center sm:px-6">
        <p className="text-muted-foreground">Product not found.</p>
        <Link href="/shop/products" className="mt-2 inline-block text-primary hover:underline">
          Back to products
        </Link>
      </div>
    );
  }

  const discount =
    product?.original_price && product.original_price > product.price
      ? Math.round((1 - product.price / product.original_price) * 100)
      : 0;

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <Link
        href="/shop/products"
        className="mb-6 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> Back to products
      </Link>

      <div className="grid gap-8 lg:grid-cols-2">
        <div className="overflow-hidden rounded-2xl bg-muted ring-1 ring-foreground/10">
          {product ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={productImageUrl(product.id, 800, 600, product.image_url, product.category)}
              alt={product.name}
              className="aspect-[4/3] w-full object-cover"
            />
          ) : (
            <Skeleton className="aspect-[4/3] w-full" />
          )}
        </div>

        <div>
          {product ? (
            <>
              {product.brand && (
                <p className="text-sm text-muted-foreground">{product.brand}</p>
              )}
              <h1 className="mt-1 text-2xl font-bold tracking-tight">{product.name}</h1>
              <div className="mt-3 flex items-center gap-3">
                <span className="text-2xl font-semibold text-primary">
                  {formatPrice(product.price)}
                </span>
                {discount > 0 && (
                  <>
                    <span className="text-muted-foreground line-through">
                      {formatPrice(product.original_price!)}
                    </span>
                    <span className="rounded-full bg-destructive px-2 py-0.5 text-xs font-semibold text-white">
                      -{discount}%
                    </span>
                  </>
                )}
              </div>
              {product.rating != null && product.rating > 0 && (
                <div className="mt-2 flex items-center gap-1 text-sm text-muted-foreground">
                  <Star className="size-4 fill-amber-400 text-amber-400" />
                  {product.rating.toFixed(1)}
                  {product.review_count ? ` · ${product.review_count} reviews` : ""}
                </div>
              )}
              {product.description && (
                <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
                  {product.description}
                </p>
              )}

              <div className="mt-6 flex flex-wrap gap-3">
                <Button
                  size="lg"
                  onClick={addToCart}
                  disabled={adding || product.in_stock === false}
                >
                  {adding ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : product.in_stock === false ? (
                    "Out of stock"
                  ) : user ? (
                    "Add to cart"
                  ) : (
                    "Sign in to buy"
                  )}
                </Button>
                <Button
                  render={
                    <Link
                      href={`/shop/assistant?prompt=${encodeURIComponent(`Tell me more about ${product.name}`)}`}
                    />
                  }
                  variant="outline"
                  size="lg"
                >
                  <Sparkles className="size-4 text-primary" /> Ask the assistant
                </Button>
              </div>
            </>
          ) : (
            <div className="space-y-3">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-8 w-3/4" />
              <Skeleton className="h-6 w-32" />
              <Skeleton className="h-24 w-full" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
