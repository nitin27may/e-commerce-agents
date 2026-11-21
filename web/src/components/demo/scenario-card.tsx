import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Scenario } from "@/lib/scenarios";

interface ScenarioCardProps {
  scenario: Scenario;
  href: string;
  /** Compact single-line layout for use inside the command palette. */
  compact?: boolean;
}

export function ScenarioCard({ scenario, href, compact = false }: ScenarioCardProps) {
  const Icon = scenario.icon;

  if (compact) {
    return (
      <Link
        href={href}
        className="flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors hover:bg-accent hover:text-accent-foreground"
      >
        <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="flex-1 font-medium">{scenario.label}</span>
        <span className="truncate text-xs text-muted-foreground">
          {scenario.description}
        </span>
        <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      </Link>
    );
  }

  return (
    <Link
      href={href}
      className="group/sc flex h-full flex-col rounded-xl bg-card p-4 ring-1 ring-foreground/10 transition-all hover:shadow-md hover:ring-primary/30"
    >
      <div className="flex items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 transition-transform group-hover/sc:scale-105">
          <Icon className="size-4 text-primary" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold leading-snug">{scenario.label}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {scenario.description}
          </p>
        </div>
      </div>

      <p className="mt-3 flex-1 line-clamp-2 rounded-md bg-muted/60 px-2.5 py-1.5 font-mono text-xs leading-relaxed text-foreground/70">
        &ldquo;{scenario.prompt}&rdquo;
      </p>

      <div className="mt-3 flex items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap gap-1">
          {scenario.agents.map((a) => (
            <span
              key={a}
              className={cn(
                "rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground",
              )}
            >
              {a}
            </span>
          ))}
        </div>
        <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary transition-colors group-hover/sc:bg-primary group-hover/sc:text-primary-foreground">
          Try it <ArrowRight className="size-3" />
        </span>
      </div>
    </Link>
  );
}
