"use client";

import { useState } from "react";
import { ChevronRight, Check, X, ChevronDown } from "lucide-react";
import type { AgentStep } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Collapsible "agent activity" timeline — renders the tool-call steps streamed
 * over `event: step` SSE frames (orchestrator → specialist → tool).
 * Each step row expands to show tool_input / tool_output as formatted JSON.
 */
export function AgentTimeline({ steps }: { steps: AgentStep[] }) {
  // Open by default — the agentic timeline is the one thing this repo
  // exists to show; hiding it behind a click buried the point.
  const [open, setOpen] = useState(true);

  if (!steps.length) return null;

  return (
    <div className="mt-2 max-w-xl rounded-lg border bg-card/60 text-xs">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />
        <span>
          Agent activity · {steps.length} step{steps.length > 1 ? "s" : ""}
        </span>
        <span className="ml-auto text-muted-foreground/60">
          {steps.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0)}ms total
        </span>
      </button>

      {open && (
        <ol className="divide-y border-t">
          {steps.map((s, i) => (
            <StepRow key={i} step={s} />
          ))}
        </ol>
      )}
    </div>
  );
}

function StepRow({ step: s }: { step: AgentStep }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = s.tool_input !== undefined || s.tool_output !== undefined;

  return (
    <li>
      <button
        type="button"
        disabled={!hasDetail}
        onClick={() => setExpanded((e) => !e)}
        className={cn(
          "flex w-full items-center gap-2 px-2.5 py-1.5 text-left",
          hasDetail ? "cursor-pointer hover:bg-muted/40" : "cursor-default"
        )}
      >
        {hasDetail && (
          <ChevronDown
            className={cn(
              "size-3 shrink-0 text-muted-foreground/60 transition-transform",
              expanded && "rotate-180"
            )}
          />
        )}
        {!hasDetail && <span className="size-3 shrink-0" />}

        <span className="rounded bg-primary/10 px-1.5 py-0.5 font-medium text-primary">
          {s.agent ?? "orchestrator"}
        </span>
        <span className="truncate font-mono text-foreground/80">{s.tool_name}</span>
        {s.status === "error" ? (
          <X className="size-3 shrink-0 text-destructive" />
        ) : (
          <Check className="size-3 shrink-0 text-emerald-500" />
        )}
        {typeof s.duration_ms === "number" && (
          <span className="ml-auto shrink-0 text-muted-foreground">{s.duration_ms}ms</span>
        )}
      </button>

      {expanded && hasDetail && (
        <div className="space-y-1.5 border-t bg-muted/20 px-3 py-2">
          {s.tool_input !== undefined && (
            <JsonBlock label="Input" value={s.tool_input} />
          )}
          {s.tool_output !== undefined && (
            <JsonBlock label="Output" value={s.tool_output} />
          )}
          {s.provenance && s.provenance.row_ids.length > 0 && (
            <p className="text-muted-foreground">
              Sourced from <span className="font-mono text-foreground/80">{s.provenance.source}</span>
              {" — "}
              {s.provenance.row_ids.length} row{s.provenance.row_ids.length === 1 ? "" : "s"}
              {": "}
              <span className="font-mono text-foreground/70">{s.provenance.row_ids.join(", ")}</span>
            </p>
          )}
        </div>
      )}
    </li>
  );
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  const text =
    typeof value === "string"
      ? value
      : JSON.stringify(value, null, 2);

  // Truncate very long outputs for readability
  const truncated = text.length > 800;
  const display = truncated ? text.slice(0, 800) + "\n…" : text;

  return (
    <div>
      <p className="mb-0.5 font-medium text-muted-foreground">{label}</p>
      <pre className="max-h-40 overflow-auto rounded bg-background/60 p-2 text-[10px] leading-relaxed text-foreground/80 ring-1 ring-border/60">
        {display}
      </pre>
    </div>
  );
}
