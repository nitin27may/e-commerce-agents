"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import {
  Store,
  ShoppingBag,
  Package,
  BadgePercent,
  Star,
  Boxes,
  LifeBuoy,
  ArrowRight,
  Workflow,
  Database,
  Cpu,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { pageEnter, listStagger, listItem, instant } from "@/lib/motion";

const REPO_URL = "https://github.com/nitin27may/e-commerce-agents";
const SERIES_URL =
  "https://nitinksingh.com/posts/building-a-multi-agent-e-commerce-platform-the-complete-guide/";

interface AgentCard {
  name: string;
  blurb: string;
  icon: LucideIcon;
}

const AGENTS: AgentCard[] = [
  { name: "Product Discovery", blurb: "Semantic search & recommendations over a pgvector catalog.", icon: ShoppingBag },
  { name: "Order Management", blurb: "Place, track, cancel, and return orders end to end.", icon: Package },
  { name: "Pricing & Promotions", blurb: "Live deals, coupons, and price breakdowns.", icon: BadgePercent },
  { name: "Reviews & Sentiment", blurb: "Summarized reviews and sentiment signals.", icon: Star },
  { name: "Inventory & Fulfillment", blurb: "Stock checks and shipping/fulfillment status.", icon: Boxes },
  { name: "Customer Support", blurb: "Help, escalation, and account questions.", icon: LifeBuoy },
];

const STACK = [
  "Microsoft Agent Framework",
  "A2A protocol",
  "Next.js 16",
  "PostgreSQL + pgvector",
  "OpenTelemetry → Aspire",
];

export function Landing() {
  const reduce = useReducedMotion();

  return (
    <div className="min-h-screen bg-gradient-to-br from-background via-background to-muted">
      {/* Nav */}
      <header className="mx-auto flex max-w-6xl items-center justify-between px-4 py-5 sm:px-6">
        <div className="flex items-center gap-2">
          <div className="flex size-8 items-center justify-center rounded-lg bg-primary">
            <Store className="size-4 text-primary-foreground" />
          </div>
          <span className="text-sm font-semibold tracking-tight">
            E-Commerce Agents
          </span>
        </div>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Button render={<Link href={REPO_URL} />} variant="ghost" size="sm">
            <span className="max-sm:sr-only">GitHub</span>
            <ArrowRight className="size-3.5" />
          </Button>
          <Button render={<Link href="/login" />} size="sm">
            Sign in
          </Button>
        </div>
      </header>

      {/* Hero */}
      <motion.section
        variants={reduce ? instant : pageEnter}
        initial="hidden"
        animate="visible"
        className="mx-auto max-w-3xl px-4 pb-10 pt-14 text-center sm:px-6 sm:pt-20"
      >
        <span className="inline-flex items-center gap-1.5 rounded-full border bg-card px-3 py-1 text-xs font-medium text-muted-foreground">
          <Cpu className="size-3.5 text-primary" />
          6 specialist agents · A2A orchestration
        </span>
        <h1 className="mt-5 text-balance text-4xl font-bold tracking-tight sm:text-5xl">
          A multi-agent platform for{" "}
          <span className="text-primary">e-commerce</span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-pretty text-base text-muted-foreground sm:text-lg">
          Product discovery, orders, pricing, reviews, inventory, and support —
          specialist AI agents that collaborate over the A2A protocol, built on
          the Microsoft Agent Framework.
        </p>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Button render={<Link href="/shop" />} size="lg">
            Try the demo <ArrowRight className="size-4" />
          </Button>
          <Button
            render={<Link href={SERIES_URL} />}
            variant="outline"
            size="lg"
          >
            Read the series
          </Button>
        </div>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-2">
          {STACK.map((s) => (
            <span
              key={s}
              className="rounded-full border bg-card/60 px-2.5 py-1 text-xs text-muted-foreground"
            >
              {s}
            </span>
          ))}
        </div>
      </motion.section>

      {/* Architecture flow */}
      <section className="mx-auto max-w-5xl px-4 py-10 sm:px-6">
        <div className="grid items-stretch gap-3 sm:grid-cols-4">
          {[
            { label: "Next.js Frontend", sub: "Chat + storefront", icon: Store },
            { label: "Orchestrator", sub: "Routes via A2A", icon: Workflow },
            { label: "6 Specialist Agents", sub: "Domain tools", icon: Cpu },
            { label: "PostgreSQL + Redis", sub: "pgvector + cache", icon: Database },
          ].map((n, i) => (
            <div key={n.label} className="relative">
              <div className="flex h-full flex-col items-center rounded-xl bg-card p-4 text-center ring-1 ring-foreground/10">
                <n.icon className="size-5 text-primary" />
                <p className="mt-2 text-sm font-semibold">{n.label}</p>
                <p className="text-xs text-muted-foreground">{n.sub}</p>
              </div>
              {i < 3 && (
                <ArrowRight className="absolute -right-2.5 top-1/2 hidden size-4 -translate-y-1/2 text-muted-foreground/50 sm:block" />
              )}
            </div>
          ))}
        </div>
      </section>

      {/* Agents grid */}
      <section className="mx-auto max-w-5xl px-4 py-10 sm:px-6">
        <h2 className="text-center text-xl font-bold tracking-tight">
          Meet the agents
        </h2>
        <p className="mt-1 text-center text-sm text-muted-foreground">
          Each is an independent microservice with its own tools and prompt.
        </p>
        <motion.div
          variants={reduce ? undefined : listStagger}
          initial={reduce ? undefined : "hidden"}
          animate={reduce ? undefined : "visible"}
          className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
        >
          {AGENTS.map((a) => (
            <motion.div
              key={a.name}
              variants={reduce ? undefined : listItem}
              className="rounded-xl bg-card p-4 ring-1 ring-foreground/10"
            >
              <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10">
                <a.icon className="size-4 text-primary" />
              </div>
              <p className="mt-3 text-sm font-semibold">{a.name}</p>
              <p className="mt-1 text-sm text-muted-foreground">{a.blurb}</p>
            </motion.div>
          ))}
        </motion.div>
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-3xl px-4 py-12 text-center sm:px-6">
        <div className="rounded-2xl bg-card p-8 ring-1 ring-foreground/10">
          <h2 className="text-xl font-bold tracking-tight">
            See the agents collaborate
          </h2>
          <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
            Sign in with a seeded demo account and ask the concierge to find a
            product, track an order, or apply a coupon.
          </p>
          <Button render={<Link href="/shop" />} size="lg" className="mt-5">
            Launch the demo <ArrowRight className="size-4" />
          </Button>
        </div>
      </section>

      <footer className="mx-auto max-w-6xl px-4 py-8 text-center text-xs text-muted-foreground sm:px-6">
        Companion demo for the AI article series on{" "}
        <Link href="https://nitinksingh.com" className="text-primary hover:underline">
          nitinksingh.com
        </Link>
      </footer>
    </div>
  );
}
