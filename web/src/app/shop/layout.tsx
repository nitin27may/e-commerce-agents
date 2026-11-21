"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Store, Search, Sparkles } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { CartProvider } from "@/lib/cart-context";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { ThemeToggle } from "@/components/ui/theme-toggle";

/**
 * Public storefront shell (no auth guard). Header adapts: "Sign in" when
 * anonymous, account avatar when logged in. Footer links back to the project.
 */
export default function ShopLayout({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const router = useRouter();
  const [q, setQ] = useState("");

  const initials = user?.name
    ? user.name.split(" ").map((n) => n[0]).join("").toUpperCase().slice(0, 2)
    : "U";

  function onSearch(e: FormEvent) {
    e.preventDefault();
    const query = q.trim();
    router.push(query ? `/shop/products?search=${encodeURIComponent(query)}` : "/shop/products");
  }

  return (
    <CartProvider>
    <div className="flex min-h-screen flex-col bg-background">
      <header className="sticky top-0 z-40 border-b bg-background/80 backdrop-blur supports-backdrop-filter:bg-background/60">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4 sm:px-6">
          <Link href="/shop" className="flex items-center gap-2">
            <div className="flex size-8 items-center justify-center rounded-lg bg-primary">
              <Store className="size-4 text-primary-foreground" />
            </div>
            <span className="hidden text-sm font-semibold tracking-tight sm:inline">
              E-Commerce Agents
            </span>
          </Link>

          <form onSubmit={onSearch} className="ml-2 flex flex-1 items-center">
            <div className="relative w-full max-w-md">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search…"
                aria-label="Search products"
                className="h-9 w-full rounded-lg border bg-muted/40 pl-9 pr-3 text-sm outline-none transition-colors focus:border-ring focus:bg-background"
              />
            </div>
          </form>

          <nav className="flex items-center gap-1.5">
            <Button render={<Link href="/shop/products" />} variant="ghost" size="sm">
              Products
            </Button>
            <ThemeToggle />
            {user ? (
              <Link href="/home" aria-label="Your account" className="rounded-full focus-visible:ring-2 focus-visible:ring-ring">
                <Avatar className="size-8">
                  <AvatarFallback className="text-xs">{initials}</AvatarFallback>
                </Avatar>
              </Link>
            ) : (
              <Button render={<Link href="/login" />} size="sm">
                Sign in
              </Button>
            )}
          </nav>
        </div>
      </header>

      <main className="flex-1">{children}</main>

      <footer className="border-t">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-3 px-4 py-6 text-sm text-muted-foreground sm:flex-row sm:px-6">
          <p className="flex items-center gap-1.5">
            <Sparkles className="size-3.5 text-primary" />
            Agentic shopping demo · powered by 6 specialist agents
          </p>
          <div className="flex items-center gap-4">
            <Link href="/" className="hover:text-foreground">About this project</Link>
            <Link
              href="https://github.com/nitin27may/e-commerce-agents"
              className="hover:text-foreground"
            >
              GitHub
            </Link>
          </div>
        </div>
      </footer>
    </div>
    </CartProvider>
  );
}
