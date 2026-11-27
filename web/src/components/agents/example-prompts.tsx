"use client";

import Link from "next/link";
import { MessageSquare } from "lucide-react";
import { chatPromptHref } from "@/lib/scenarios";
import type { AgentMeta } from "@/lib/agents";

interface ExamplePromptsProps {
  agent: AgentMeta;
}

export function ExamplePrompts({ agent }: ExamplePromptsProps) {
  return (
    <section>
      <h2 className="mb-3 text-base font-semibold text-foreground">
        Example prompts
      </h2>
      <div className="flex flex-wrap gap-2">
        {agent.examplePrompts.map((prompt) => (
          <Link
            key={prompt}
            href={chatPromptHref(prompt)}
            className="inline-flex items-center gap-1.5 rounded-full border bg-card px-3 py-1.5 text-sm text-foreground/80 transition-colors hover:border-primary/40 hover:bg-accent hover:text-foreground"
          >
            <MessageSquare className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            {prompt}
          </Link>
        ))}
      </div>
    </section>
  );
}
