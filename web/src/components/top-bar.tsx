"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Search, ChevronRight } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { openCommandPalette } from "@/components/command-palette";
import { labelForPath } from "@/lib/nav";

/** Desktop top app bar: breadcrumb + global search + theme toggle + avatar. */
export function TopBar() {
  const pathname = usePathname();
  const { user } = useAuth();
  const title = labelForPath(pathname);

  const initials = user?.name
    ? user.name
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2)
    : "U";

  return (
    <header className="hidden h-14 shrink-0 items-center gap-3 border-b bg-background px-4 lg:flex">
      <nav className="flex items-center gap-1.5 text-sm" aria-label="Breadcrumb">
        <span className="text-muted-foreground">E-Commerce Agents</span>
        <ChevronRight className="size-3.5 text-muted-foreground/50" />
        <span className="font-medium">{title}</span>
      </nav>

      <div className="ml-auto flex items-center gap-2">
        <button
          type="button"
          onClick={openCommandPalette}
          className="flex items-center gap-2 rounded-lg border bg-muted/40 px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted"
        >
          <Search className="size-4" />
          {/* The visible text is the accessible name (WCAG 2.5.3); below md it
              is hidden visually but still announced. */}
          <span className="max-md:sr-only">Search…</span>
          <kbd aria-hidden="true" className="ml-2 hidden rounded border bg-background px-1.5 py-0.5 text-[10px] md:inline">
            ⌘K
          </kbd>
        </button>

        <ThemeToggle />

        <Link
          href="/profile"
          className="rounded-full ring-offset-background transition hover:opacity-90 focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Avatar className="size-8">
            <AvatarFallback className="text-xs">{initials}</AvatarFallback>
          </Avatar>
          {/* Name comes from content (initials + "Profile") so the visible text
              is part of the accessible name (WCAG 2.5.3). */}
          <span className="sr-only">Profile</span>
        </Link>
      </div>
    </header>
  );
}
