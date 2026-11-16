"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { CartProvider } from "@/lib/cart-context";
import { DesktopSidebar, MobileSidebar } from "@/components/sidebar";
import { TopBar } from "@/components/top-bar";
import { CommandPalette, openCommandPalette } from "@/components/command-palette";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { Button } from "@/components/ui/button";
import { Search } from "lucide-react";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !user) {
      router.replace("/login");
    }
  }, [user, isLoading, router]);

  // Show nothing while checking auth state
  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="size-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  // Don't render the app shell until we confirm the user is authenticated
  if (!user) {
    return null;
  }

  return (
    <CartProvider>
      {/* First tab stop: lets keyboard and screen-reader users jump past the
          sidebar and top bar. Visually hidden until focused. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-primary-foreground focus:shadow-lg focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
      >
        Skip to main content
      </a>
      <CommandPalette />
      <div className="flex h-screen overflow-hidden">
        <DesktopSidebar />
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* Mobile header */}
          <header className="flex h-14 items-center gap-2 border-b bg-background px-4 lg:hidden">
            <MobileSidebar />
            <span className="truncate text-sm font-semibold">E-Commerce Agents</span>
            {/* Search and theme live in the desktop top bar, which is hidden
                below lg — without these a phone has neither. */}
            <div className="ml-auto flex items-center gap-1">
              <Button variant="ghost" size="icon" aria-label="Search" onClick={openCommandPalette}>
                <Search className="size-5" />
              </Button>
              <ThemeToggle />
            </div>
          </header>
          {/* Desktop top bar */}
          <TopBar />
          {/* Main content */}
          <main id="main-content" tabIndex={-1} className="flex-1 overflow-y-auto focus:outline-none">
            {children}
          </main>
        </div>
      </div>
    </CartProvider>
  );
}
