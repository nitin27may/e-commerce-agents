"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Bot } from "lucide-react";
import { AGENTS } from "@/lib/agents";
import { pageEnter, listStagger, listItem, instant } from "@/lib/motion";
import { PageContainer, PageHeader } from "@/components/ui/page-header";
import { StaffOnly } from "@/components/staff-only";
import { specialistSummary, toolCount } from "@/components/agents/agent-copy";

export default function AgentsIndexPage() {
  return (
    <StaffOnly>
      <AgentsIndex />
    </StaffOnly>
  );
}

function AgentsIndex() {
  const reduce = useReducedMotion();
  const specialists = AGENTS.filter((a) => a.slug !== "orchestrator").length;

  return (
    <motion.div variants={reduce ? instant : pageEnter} initial="hidden" animate="visible">
      <PageContainer>
        <PageHeader icon={Bot} title="Agents" description={specialistSummary(specialists)} />

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
                  className="group/card flex h-full flex-col rounded-xl bg-card p-5 ring-1 ring-foreground/10 transition-shadow hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <div className="flex items-start gap-3">
                    <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <Icon className="size-5" aria-hidden />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold">{agent.name}</p>
                      <p className="text-xs text-muted-foreground">{agent.role}</p>
                    </div>
                  </div>

                  <p className="mt-3 line-clamp-3 flex-1 text-sm leading-relaxed text-muted-foreground">
                    {agent.tagline}
                  </p>

                  <div className="mt-4 flex items-center justify-between border-t pt-3">
                    <span className="text-xs text-muted-foreground">{toolCount(agent.tools.length)}</span>
                    <span className="inline-flex items-center gap-1 text-xs font-medium text-primary">
                      View details <ArrowRight className="size-3 transition-transform group-hover/card:translate-x-0.5" />
                    </span>
                  </div>
                </Link>
              </motion.div>
            );
          })}
        </motion.div>
      </PageContainer>
    </motion.div>
  );
}
