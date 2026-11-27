"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useLoadEffect } from "@/lib/use-load-effect";
import { useAuth } from "@/lib/auth-context";
import { api, type SellerProduct } from "@/lib/api";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/ui/status-badge";
import { PageContainer, PageHeader } from "@/components/ui/page-header";
import { AccessDenied } from "@/components/ui/access-denied";
import { EmptyState } from "@/components/empty-state";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Package,
  Plus,
  Star,
  Loader2,
  CheckCircle,
  XCircle,
} from "lucide-react";
import { productImageUrl } from "@/lib/images";
import { formatPrice } from "@/lib/format";

export default function SellerProductsPage() {
  const router = useRouter();
  const { user, isLoading: authLoading, isAdmin } = useAuth();

  const [products, setProducts] = useState<SellerProduct[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isSeller = user?.role === "seller" || isAdmin;

  const loadProducts = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getSellerProducts();
      setProducts(data.products);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load products",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useLoadEffect(loadProducts, Boolean(user && isSeller));

  if (authLoading) return null;

  if (!user || !isSeller) {
    return <AccessDenied description="Product management is only available to sellers and admins." />;
  }

  return (
    <PageContainer>
      <PageHeader
        icon={Package}
        title="Your products"
        description={loading ? "Your catalogue" : `${products.length} products in your catalogue`}
        actions={
          <Dialog>
            <DialogTrigger render={<Button />}>
              <Plus />
              Add product
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Coming soon</DialogTitle>
                <DialogDescription>
                  Product creation is not yet available. Full seller
                  capabilities including product creation, editing,
                  and inventory management are on the roadmap.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter showCloseButton>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />

        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {/* Empty */}
        {!loading && !error && products.length === 0 && (
          <EmptyState icon={Package} title="No products yet" description="Products you list will show up here." />
        )}

        {/* Product table */}
        {!loading && !error && products.length > 0 && (
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[60px]"><span className="sr-only">Image</span></TableHead>
                    <TableHead>Product</TableHead>
                    <TableHead>Category</TableHead>
                    <TableHead className="text-right">Price</TableHead>
                    <TableHead className="text-right">Rating</TableHead>
                    <TableHead className="text-center">Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {products.map((product) => (
                    <TableRow
                      key={product.id}
                      className="cursor-pointer"
                      onClick={() =>
                        router.push(`/products/${product.id}`)
                      }
                    >
                      <TableCell>
                        <img
                          src={productImageUrl(product.id, 48, 48, product.image_url, product.category, product.name)}
                          alt={product.name}
                          className="size-10 rounded-md object-cover bg-muted"
                        />
                      </TableCell>
                      <TableCell>
                        <div>
                          <p className="font-medium text-foreground">
                            {product.name}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {product.brand}
                          </p>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {product.category}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right">
                        <div>
                          <span className="font-medium text-foreground">
                            {formatPrice(product.price)}
                          </span>
                          {product.original_price &&
                            product.original_price > product.price && (
                              <span className="ml-1 text-xs text-muted-foreground line-through">
                                {formatPrice(product.original_price)}
                              </span>
                            )}
                        </div>
                      </TableCell>
                      <TableCell className="text-right">
                        <span className="flex items-center justify-end gap-1 text-xs">
                          <Star className="size-3 fill-warning text-warning" aria-hidden />
                          {product.rating?.toFixed(1) ?? "N/A"}
                          <span className="text-muted-foreground">
                            ({product.review_count ?? 0})
                          </span>
                        </span>
                      </TableCell>
                      <TableCell className="text-center">
                        {product.is_active ? (
                          <StatusBadge label="Active" tone="success" icon={CheckCircle} />
                        ) : (
                          <StatusBadge label="Inactive" tone="neutral" icon={XCircle} />
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}
    </PageContainer>
  );
}
