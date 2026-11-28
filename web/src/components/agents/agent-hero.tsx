import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "@/components/ui/page-header";
import { CHIP_CLASS } from "@/components/ui/status-badge";
import { Badge } from "@/components/ui/badge";
import type { AgentMeta } from "@/lib/agents";

interface AgentHeroProps {
  agent: AgentMeta;
}

export function AgentHero({ agent }: AgentHeroProps) {
  return (
    <div>
      <Link
        href="/agents"
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        All agents
      </Link>

      <PageHeader
        icon={agent.icon}
        title={
          <span className="flex flex-wrap items-center gap-2">
            {agent.name}
            <Badge variant="outline" className={CHIP_CLASS.primary}>
              {agent.role}
            </Badge>
          </span>
        }
        description={agent.tagline}
        className="mb-4"
      />

      <p className="max-w-3xl text-sm leading-relaxed text-foreground/80">{agent.description}</p>
    </div>
  );
}
