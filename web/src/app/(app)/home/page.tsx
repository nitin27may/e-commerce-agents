"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import {
  Package,
  ShoppingCart,
  Bot,
  Sparkles,
  ArrowRight,
  Activity,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useCart } from "@/lib/cart-context";
import { api, type OrderSummary as HomeOrder } from "@/lib/api";
import { formatPrice, formatDate } from "@/lib/format";
import { productImageUrl } from "@/lib/images";
import { QUICK_PROMPTS, DEMO_SCENARIOS, chatPromptHref } from "@/lib/scenarios";
import { pageEnter, listStagger, listItem, instant } from "@/lib/motion";
import { StatCard } from "@/components/ui/stat-card";
import { SectionHeader } from "@/components/ui/section-header";
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
  const [products, setProducts] = useState<HomeProduct[] | null>(null);

  useEffect(() => {
    api
      .getOrders()
      .then((r) => setOrders(((r?.orders ?? []) as HomeOrder[]).slice(0, 4)))
      .catch(() => setOrders([]));
    api
      .getProducts({ sort: "rating" })
      .then((r) =>
        setProducts(((r?.products ?? []) as HomeProduct[]).slice(0, 4)),
      )
      .catch(() => setProducts([]));
  }, []);

  const firstName = user?.name?.split(" ")[0] ?? "there";

  return (
    <motion.div
      variants={reduce ? instant : pageEnter}
      initial="hidden"
      animate="visible"
      className="mx-auto max-w-7xl space-y-8 px-4 py-8 sm:px-6 lg:px-8"
    >
      {/* Greeting + quick prompts */}
      <div>
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {formatDate(new Date().toISOString())}
        </p>
        <h1 className="mt-1 flex items-center gap-2 text-2xl font-bold tracking-tight">
          <Sparkles className="size-6 text-primary" />
          Good {timeGreeting()}, {firstName}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Your multi-agent shopping concierge. Ask anything, or jump back in.
        </p>

        <div className="mt-4 flex flex-wrap gap-2">
          {QUICK_PROMPTS.map((s) => (
            <Link
              key={s.label}
              href={chatPromptHref(s.prompt)}
              className="rounded-full border bg-card px-3 py-1.5 text-sm text-foreground/80 transition-colors hover:border-primary/40 hover:bg-accent hover:text-foreground"
            >
              {s.label}
            </Link>
          ))}
          <Link
            href="/chat"
            className="inline-flex items-center gap-1 rounded-full bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
          >
            Open chat <ArrowRight className="size-3.5" />
          </Link>
        </div>
      </div>

      {/* Stat row */}
      <motion.div
        variants={reduce ? undefined : listStagger}
        initial={reduce ? undefined : "hidden"}
        animate={reduce ? undefined : "visible"}
        className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"
      >
        {[
          {
            label: "Your Orders",
            value: orders == null ? "—" : orders.length,
            icon: Package,
            hint: "recent",
          },
          {
            label: "Cart Items",
            value: cart?.item_count ?? 0,
            icon: ShoppingCart,
            hint: cart?.subtotal ? formatPrice(cart.subtotal) : "empty",
          },
          {
            label: "Specialist Agents",
            value: 6,
            icon: Bot,
            hint: "collaborating",
          },
          {
            label: "Avg Response",
            value: "~1.2s",
            icon: Activity,
            hint: "across agents",
          },
        ].map((s) => (
          <motion.div key={s.label} variants={reduce ? undefined : listItem}>
            <StatCard
              label={s.label}
              value={s.value}
              icon={s.icon}
              hint={s.hint}
            />
          </motion.div>
        ))}
      </motion.div>

      {/* Main grid */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Recent orders */}
        <div className="rounded-xl bg-card ring-1 ring-foreground/10 lg:col-span-2">
          <div className="flex items-center justify-between px-4 py-3">
            <h2 className="text-sm font-semibold">Recent Orders</h2>
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
              <div className="px-4 py-10 text-center text-sm text-muted-foreground">
                No orders yet.{" "}
                <Link href="/products" className="text-primary hover:underline">
                  Browse products
                </Link>
              </div>
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
                        <span className="text-sm font-medium tabular-nums">
                          {formatPrice(o.total)}
                        </span>
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {/* Cart snapshot + agent activity */}
        <div className="space-y-6">
          <div className="rounded-xl bg-card p-4 ring-1 ring-foreground/10">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">Your Cart</h2>
              <ShoppingCart className="size-4 text-muted-foreground" />
            </div>
            <p className="mt-3 text-2xl font-semibold tabular-nums">
              {cart?.item_count ?? 0}{" "}
              <span className="text-sm font-normal text-muted-foreground">
                items
              </span>
            </p>
            <p className="text-sm text-muted-foreground">
              Subtotal {formatPrice(cart?.subtotal ?? 0)}
            </p>
            <Link
              href="/cart"
              className="mt-4 inline-flex w-full items-center justify-center gap-1 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
            >
              Go to cart <ArrowRight className="size-3.5" />
            </Link>
          </div>

          <div className="rounded-xl border border-dashed bg-card/50 p-4">
            <div className="flex items-center gap-2">
              <Activity className="size-4 text-primary" />
              <h2 className="text-sm font-semibold">Agent Activity</h2>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              A live timeline of which specialists handled your requests is
              coming soon.
            </p>
          </div>
        </div>
      </div>

      {/* Demo scenarios */}
      <div>
        <SectionHeader
          eyebrow="See the agents in action"
          title="Demo Scenarios"
          action={
            <Link
              href="/agents"
              className="text-xs font-medium text-primary hover:underline"
            >
              View all agents
            </Link>
          }
        />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {DEMO_SCENARIOS.map((s) => (
            <ScenarioCard
              key={s.id}
              scenario={s}
              href={chatPromptHref(s.prompt)}
            />
          ))}
        </div>
      </div>

      {/* Recommended */}
      <div>
        <SectionHeader
          eyebrow="Picked for you"
          title="Recommended Products"
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
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={productImageUrl(p.id, 400, 300, p.image_url, p.category)}
                    alt={p.name}
                    className="h-36 w-full object-cover"
                  />
                  <div className="p-3">
                    <p className="line-clamp-1 text-sm font-medium">{p.name}</p>
                    {p.brand && (
                      <p className="text-xs text-muted-foreground">{p.brand}</p>
                    )}
                    <p className="mt-1 text-sm font-semibold text-primary">
                      {formatPrice(p.price)}
                    </p>
                  </div>
                </Link>
              ))}
        </div>
      </div>
    </motion.div>
  );
}
