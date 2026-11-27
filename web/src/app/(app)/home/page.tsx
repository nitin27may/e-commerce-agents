"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import { Bot, Package, ShoppingCart, ArrowRight } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useCart } from "@/lib/cart-context";
import { api, type OrderSummary as HomeOrder } from "@/lib/api";
import { formatPrice, formatDate } from "@/lib/format";
import { productImageUrl } from "@/lib/images";
import { QUICK_PROMPTS, DEMO_SCENARIOS, chatPromptHref } from "@/lib/scenarios";
import { pageEnter, instant } from "@/lib/motion";
import { StatCard } from "@/components/ui/stat-card";
import { SectionHeader } from "@/components/ui/section-header";
import { PageContainer, PageHeader } from "@/components/ui/page-header";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { OrderStatusBadge } from "@/components/status-badge";
import { ScenarioCard } from "@/components/demo/scenario-card";


interface HomeProduct {
  id: string;
  name: string;
  price: number;
  image_url?: string | null;
  category?: string | null;
  brand?: string | null;
}

function timeGreeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "morning";
  if (h < 18) return "afternoon";
  return "evening";
}

export default function HomePage() {
  const { user } = useAuth();
  const { cart } = useCart();
  const reduce = useReducedMotion();

  const [orders, setOrders] = useState<HomeOrder[] | null>(null);
  const [orderTotal, setOrderTotal] = useState<number | null>(null);
  const [products, setProducts] = useState<HomeProduct[] | null>(null);

  useEffect(() => {
    api
      .getOrders()
      .then((r) => {
        const all = (r?.orders ?? []) as HomeOrder[];
        setOrders(all.slice(0, 4));
        setOrderTotal(r?.total ?? all.length);
      })
      .catch(() => {
        setOrders([]);
        setOrderTotal(0);
      });
    api
      .getProducts({ sort: "rating" })
      .then((r) =>
        setProducts(((r?.products ?? []) as HomeProduct[]).slice(0, 4)),
      )
      .catch(() => setProducts([]));
  }, []);

  const firstName = user?.name?.split(" ")[0] ?? "there";
  const itemCount = cart?.item_count ?? 0;

  return (
    <PageContainer>
      <motion.div
        variants={reduce ? instant : pageEnter}
        initial="hidden"
        animate="visible"
        className="space-y-8"
      >
        <div>
          <PageHeader
            icon={Bot}
            title={`Good ${timeGreeting()}, ${firstName}`}
            description="Your shopping assistant. Ask anything, or pick up where you left off."
            actions={
              <Link href="/chat" className={cn(buttonVariants())}>
                Open chat <ArrowRight data-icon="inline-end" />
              </Link>
            }
            className="mb-4"
          />
          <div className="flex flex-wrap gap-2">
            {QUICK_PROMPTS.map((s) => (
              <Link
                key={s.label}
                href={chatPromptHref(s.prompt)}
                className="rounded-full border bg-card px-3 py-1.5 text-sm text-foreground/80 transition-colors hover:border-primary/40 hover:bg-accent hover:text-foreground"
              >
                {s.label}
              </Link>
            ))}
          </div>
        </div>

        <div className="grid gap-6 lg:grid-cols-3">
          {/* Recent orders */}
          <section className="rounded-xl bg-card ring-1 ring-foreground/10 lg:col-span-2">
            <div className="flex items-center justify-between px-4 py-3">
              <h2 className="text-sm font-semibold">Recent orders</h2>
              <Link
                href="/orders"
                className="text-xs font-medium text-primary hover:underline"
              >
                View all
              </Link>
            </div>
            <div className="border-t">
              {orders == null ? (
                <div className="space-y-3 p-4">
                  {[0, 1, 2].map((i) => (
                    <Skeleton key={i} className="h-12 w-full" />
                  ))}
                </div>
              ) : orders.length === 0 ? (
                <EmptyState
                  icon={Package}
                  title="No orders yet"
                  description="Orders you place show up here."
                  className="m-4 py-10"
                >
                  <Link href="/products" className={cn(buttonVariants({ variant: "outline" }))}>
                    Browse products
                  </Link>
                </EmptyState>
              ) : (
                <ul className="divide-y">
                  {orders.map((o) => (
                    <li key={o.id}>
                      <Link
                        href={`/orders/${o.id}`}
                        className="flex items-center justify-between gap-3 px-4 py-3 transition-colors hover:bg-accent/50"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">
                            Order #{o.id.slice(0, 8)}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {formatDate(o.date)}
                          </p>
                        </div>
                        <div className="flex shrink-0 items-center gap-3">
                          <OrderStatusBadge status={o.status} />
                          <span className="w-20 text-right text-sm font-medium tabular-nums">
                            {formatPrice(o.total)}
                          </span>
                        </div>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>

          {/* Account snapshot */}
          <div className="grid content-start gap-4 sm:grid-cols-2 lg:grid-cols-1">
            <StatCard
              label="Orders"
              value={orderTotal ?? "—"}
              icon={Package}
              hint={orderTotal === 1 ? "order placed" : "orders placed"}
            />
            <div className="flex flex-col gap-3">
              <StatCard
                label="In your cart"
                value={itemCount}
                icon={ShoppingCart}
                hint={itemCount > 0 ? `Subtotal ${formatPrice(cart?.subtotal ?? 0)}` : "Your cart is empty"}
              />
              <Link href="/cart" className={cn(buttonVariants({ variant: "outline", className: "w-full" }))}>
                Go to cart <ArrowRight data-icon="inline-end" />
              </Link>
            </div>
          </div>
        </div>

        {/* Demo scenarios */}
        <section>
          <SectionHeader title="Things you can ask" description="Start a conversation with one of these." />
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {DEMO_SCENARIOS.map((s) => (
              <ScenarioCard
                key={s.id}
                scenario={s}
                href={chatPromptHref(s.prompt)}
              />
            ))}
          </div>
        </section>

        {/* Recommended */}
        <section>
          <SectionHeader
            title="Recommended for you"
            description="Top-rated picks from the catalog."
            action={
              <Link
                href="/products"
                className="text-xs font-medium text-primary hover:underline"
              >
                Browse all
              </Link>
            }
          />
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {products == null
              ? [0, 1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-56 w-full rounded-xl" />
                ))
              : products.map((p) => (
                  <Link
                    key={p.id}
                    href={`/products/${p.id}`}
                    className="group/card overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10 transition-shadow hover:shadow-md"
                  >
                    <div className="aspect-[4/3] overflow-hidden bg-muted">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={productImageUrl(p.id, 400, 300, p.image_url, p.category, p.name)}
                        alt={p.name}
                        className="size-full object-cover transition-transform duration-300 group-hover/card:scale-105"
                      />
                    </div>
                    <div className="p-3">
                      {p.brand && (
                        <p className="text-xs text-muted-foreground">{p.brand}</p>
                      )}
                      <p className="line-clamp-1 text-sm font-medium">{p.name}</p>
                      <p className="mt-1 text-sm font-semibold">
                        {formatPrice(p.price)}
                      </p>
                    </div>
                  </Link>
                ))}
          </div>
        </section>
      </motion.div>
    </PageContainer>
  );
}
