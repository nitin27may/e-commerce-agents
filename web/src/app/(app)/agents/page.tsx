"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import { ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { AGENTS } from "@/lib/agents";
import { pageEnter, listStagger, listItem, instant } from "@/lib/motion";
import { SectionHeader } from "@/components/ui/section-header";

export default function AgentsIndexPage() {
  const reduce = useReducedMotion();

  return (
    <motion.div
      variants={reduce ? instant : pageEnter}
      initial="hidden"
      animate="visible"
      className="mx-auto max-w-7xl space-y-8 px-4 py-8 sm:px-6 lg:px-8"
    >
      <SectionHeader
        as="h1"
        eyebrow="Multi-agent platform"
        title="Specialist Agents"
      />
      <p className="max-w-2xl text-sm text-muted-foreground">
        Six specialist agents collaborate to handle every part of the shopping
        experience. Each has a dedicated tool set and is called by the
        Orchestrator via the A2A protocol.
      </p>

      <motion.div
        variants={reduce ? undefined : listStagger}
        initial={reduce ? undefined : "hidden"}
        animate={reduce ? undefined : "visible"}
        className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
      >
        {AGENTS.map((agent) => {
          const Icon = agent.icon;
          return (
            <motion.div key={agent.slug} variants={reduce ? undefined : listItem}>
              <Link
                href={`/agents/${agent.slug}`}
                className="group/card flex h-full flex-col rounded-xl bg-card p-5 ring-1 ring-foreground/10 transition-shadow hover:shadow-md"
              >
                <div className="flex items-start gap-4">
                  <div
                    className={cn(
                      "flex size-11 shrink-0 items-center justify-center rounded-xl transition-transform group-hover/card:scale-105",
                      agent.accentBg,
                    )}
                  >
                    <Icon className={cn("size-5", agent.accentText)} aria-hidden />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold">{agent.name}</p>
                    <p className={cn("text-xs font-medium", agent.accentText)}>
                      {agent.role}
                    </p>
                  </div>
                </div>

                <p className="mt-3 flex-1 text-xs leading-relaxed text-muted-foreground line-clamp-3">
                  {agent.tagline}
                </p>

                <div className="mt-4 flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    {agent.tools.length} tools
                  </span>
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-primary opacity-0 transition-opacity group-hover/card:opacity-100">
                    View details <ArrowRight className="size-3" />
                  </span>
                </div>
              </Link>
            </motion.div>
          );
        })}
      </motion.div>
    </motion.div>
  );
}
