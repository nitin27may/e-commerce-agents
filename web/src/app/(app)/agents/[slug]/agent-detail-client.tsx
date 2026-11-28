"use client";

import { useEffect, useState } from "react";
import { notFound } from "next/navigation";
import { motion, useReducedMotion } from "framer-motion";
import { getAgent } from "@/lib/agents";
import { api } from "@/lib/api";
import { pageEnter, instant } from "@/lib/motion";
import { PageContainer } from "@/components/ui/page-header";
import { AgentHero } from "@/components/agents/agent-hero";
import { ToolGrid } from "@/components/agents/tool-grid";
import { ExamplePrompts } from "@/components/agents/example-prompts";
import { AgentStatsStrip } from "@/components/agents/agent-stats-strip";

type AgentStat = {
  agent_name: string;
  request_count: number;
  avg_duration_ms: number;
  total_tokens: number;
};

interface Props {
  slug: string;
}

export function AgentDetailClient({ slug }: Props) {
  const reduce = useReducedMotion();
  const [stats, setStats] = useState<AgentStat[] | null>(null);

  const agent = getAgent(slug);

  useEffect(() => {
    api
      .getAgentStats()
      .then((rows) => setStats(rows ?? []))
      .catch(() => setStats([]));
  }, []);

  if (!agent) {
    notFound();
  }

  return (
    <motion.div variants={reduce ? instant : pageEnter} initial="hidden" animate="visible">
      <PageContainer className="space-y-8">
        <AgentHero agent={agent} />

        <AgentStatsStrip backendName={agent.backendName} stats={stats} />

        <ExamplePrompts agent={agent} />

        <ToolGrid agent={agent} />
      </PageContainer>
    </motion.div>
  );
}
