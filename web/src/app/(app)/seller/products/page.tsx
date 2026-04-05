"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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

  const [products, setProducts] = useState<any[]>([]);
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

  useEffect(() => {
    if (user && isSeller) loadProducts();
  }, [user, isSeller, loadProducts]);

  if (authLoading) return null;

  if (!user || !isSeller) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-center">
          <Package className="mx-auto size-12 text-slate-300" />
          <h2 className="mt-4 text-lg font-semibold text-slate-800">
            Access Denied
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Product management is only available to sellers and admins.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-lg bg-teal-600">
                <Package className="size-5 text-white" />
              </div>
              <div>
                <h1 className="text-2xl font-bold text-slate-900">
                  My Products
                </h1>
                <p className="text-sm text-slate-500">
                  {products.length} products in your catalog
                </p>
              </div>
            </div>

            <Dialog>
              <DialogTrigger
                render={
                  <Button className="bg-teal-600 text-white hover:bg-teal-700" />
                }
              >
                <Plus className="mr-2 size-4" />
                Add Product
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Coming Soon</DialogTitle>
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
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="size-6 animate-spin text-slate-400" />
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {/* Empty */}
        {!loading && !error && products.length === 0 && (
          <div className="py-20 text-center">
            <Package className="mx-auto size-10 text-slate-300" />
            <p className="mt-3 text-sm text-slate-500">
              You have no products yet.
            </p>
          </div>
        )}

        {/* Product table */}
        {!loading && !error && products.length > 0 && (
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[60px]">Image</TableHead>
                    <TableHead>Product</TableHead>
                    <TableHead>Category</TableHead>
                    <TableHead className="text-right">Price</TableHead>
                    <TableHead className="text-right">Rating</TableHead>
                    <TableHead className="text-center">Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {products.map((product: any) => (
                    <TableRow
                      key={product.id}
                      className="cursor-pointer"
                      onClick={() =>
                        router.push(`/products/${product.id}`)
                      }
                    >
                      <TableCell>
                        <img
                          src={productImageUrl(product.id, 48, 48)}
                          alt={product.name}
                          className="size-10 rounded-md object-cover bg-slate-100"
                        />
                      </TableCell>
                      <TableCell>
                        <div>
                          <p className="font-medium text-slate-800">
                            {product.name}
                          </p>
                          <p className="text-xs text-slate-500">
                            {product.brand}
                          </p>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-[10px]">
                          {product.category}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right">
                        <div>
                          <span className="font-medium text-slate-800">
                            {formatPrice(product.price)}
                          </span>
                          {product.original_price &&
                            product.original_price > product.price && (
                              <span className="ml-1 text-xs text-slate-400 line-through">
                                {formatPrice(product.original_price)}
                              </span>
                            )}
                        </div>
                      </TableCell>
                      <TableCell className="text-right">
                        <span className="flex items-center justify-end gap-1 text-xs">
                          <Star className="size-3 fill-amber-400 text-amber-400" />
                          {product.rating?.toFixed(1) ?? "N/A"}
                          <span className="text-slate-400">
                            ({product.review_count ?? 0})
                          </span>
                        </span>
                      </TableCell>
                      <TableCell className="text-center">
                        {product.is_active ? (
                          <span className="inline-flex items-center gap-1 text-xs text-green-700">
                            <CheckCircle className="size-3" />
                            Active
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs text-red-600">
                            <XCircle className="size-3" />
                            Inactive
                          </span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
