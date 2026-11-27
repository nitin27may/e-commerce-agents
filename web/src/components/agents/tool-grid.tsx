import { Wrench } from "lucide-react";
import type { AgentMeta, AgentTool } from "@/lib/agents";

function ToolCard({ tool }: { tool: AgentTool }) {
  return (
    <div className="rounded-xl bg-card p-4 ring-1 ring-foreground/10 transition-shadow hover:shadow-sm">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Wrench className="size-3.5" aria-hidden />
        </div>
        <div className="min-w-0">
          <p className="font-mono text-xs font-semibold text-foreground/90">{tool.name}</p>
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{tool.description}</p>
        </div>
      </div>
    </div>
  );
}

interface ToolGridProps {
  agent: AgentMeta;
}

export function ToolGrid({ agent }: ToolGridProps) {
  return (
    <section>
      <h2 className="mb-3 text-base font-semibold text-foreground">
        Tools
        <span className="ml-2 rounded-full bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground">
          {agent.tools.length}
        </span>
      </h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {agent.tools.map((tool) => (
          <ToolCard key={tool.name} tool={tool} />
        ))}
      </div>
    </section>
  );
}
