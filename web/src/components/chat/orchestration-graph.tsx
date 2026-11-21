"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

// House Mermaid palette (tutorials/_shared/mermaid-style-guide.md): blue core,
// amber active, green done, red error. `core` is the default (unvisited) node
// state; `active`/`success`/`error` are live overlays applied per node id as SSE
// events arrive. Each theme gets its own line, text and stroke colours so edges
// and labels stay legible on the dark card; node fills are shared, and the
// green and red are dark enough for white text at 4.5:1.
type GraphTheme = "light" | "dark";

const PALETTE: Record<GraphTheme, { line: string; text: string; strokes: Record<"core" | "active" | "success" | "error", string> }> = {
  light: {
    line: "#475569",
    text: "#0f172a",
    strokes: { core: "#1e40af", active: "#b45309", success: "#065f46", error: "#991b1b" },
  },
  dark: {
    line: "#94a3b8",
    text: "#e2e8f0",
    strokes: { core: "#93c5fd", active: "#fcd34d", success: "#6ee7b7", error: "#fca5a5" },
  },
};

function mermaidInit(theme: GraphTheme): string {
  const p = PALETTE[theme];
  return `%%{init: {'theme':'base', 'themeVariables': {
  'primaryColor': '#2563eb',
  'primaryTextColor': '#ffffff',
  'primaryBorderColor': '${p.strokes.core}',
  'lineColor': '${p.line}',
  'textColor': '${p.text}',
  'secondaryColor': '#f59e0b',
  'tertiaryColor': '#047857',
  'background': 'transparent'
}}}%%`;
}

function classDefs(theme: GraphTheme): string {
  const s = PALETTE[theme].strokes;
  return [
    `classDef core fill:#2563eb,stroke:${s.core},color:#ffffff`,
    `classDef active fill:#f59e0b,stroke:${s.active},color:#000000`,
    `classDef success fill:#047857,stroke:${s.success},color:#ffffff`,
    `classDef error fill:#dc2626,stroke:${s.error},color:#ffffff`,
  ].join("\n");
}

/** Follows the `dark` class the root layout puts on <html>, and re-renders when it toggles. */
function useGraphTheme(): GraphTheme {
  const [theme, setTheme] = useState<GraphTheme>("light");
  useEffect(() => {
    const root = document.documentElement;
    const read = () => setTheme(root.classList.contains("dark") ? "dark" : "light");
    read();
    const observer = new MutationObserver(read);
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return theme;
}

let diagramCounter = 0;

interface OrchestrationGraphProps {
  /** An OrchestrationMode.name — fetches that mode's static graph_mermaid(). */
  mode: string;
  /** Live executor ids (dashed, as emitted by the backend) currently mid-run. */
  activeNodeIds?: string[];
  /** Live executor ids that have completed. */
  doneNodeIds?: string[];
  /** Live executor ids that errored. */
  errorNodeIds?: string[];
}

/**
 * A mode's node_id.replace(/-/g, "_") == the Mermaid diagram's node id —
 * a deliberate, systematic convention on the backend (see
 * orchestrator/modes/workflow_mode.py::PrePurchaseMode.graph_mermaid()'s
 * comment) specifically so a client can correlate live SSE `node` events
 * to a diagram node without a hardcoded per-mode alias table.
 */
function toMermaidId(nodeId: string): string {
  return nodeId.replace(/-/g, "_");
}

/** Every node id referenced by either side of a `-->` edge in the source. */
function extractNodeIds(source: string): string[] {
  const ids = new Set<string>();
  const edgeRe = /(\w+)(?:\[[^\]]*\])?\s*-->\s*(\w+)(?:\[[^\]]*\])?/g;
  let match: RegExpExecArray | null;
  while ((match = edgeRe.exec(source))) {
    ids.add(match[1]);
    ids.add(match[2]);
  }
  return Array.from(ids);
}

function resolveClass(id: string, active: Set<string>, done: Set<string>, error: Set<string>): string {
  if (error.has(id)) return "error";
  if (active.has(id)) return "active";
  if (done.has(id)) return "success";
  return "core";
}

/**
 * Live-animated Mermaid render of a mode's fixed graph, beside the chat
 * stream. Renders nothing for a mode with no fixed topology (`tool`,
 * `handoff` — per-turn routing, not a graph) or while the mode's graph
 * hasn't loaded yet.
 *
 * The rendered SVG is held in React state and painted via
 * `dangerouslySetInnerHTML`, not written through a ref — verified live
 * that mutating a ref'd container's `innerHTML` directly gets silently
 * discarded by a later React re-render (React has no idea the mutation
 * happened, since the JSX for that node never itself changes, and a
 * subsequent commit can still reset it). Routing the SVG through state
 * makes React the one true owner of what's on screen.
 */
export function OrchestrationGraph({
  mode,
  activeNodeIds = [],
  doneNodeIds = [],
  errorNodeIds = [],
}: OrchestrationGraphProps) {
  const [source, setSource] = useState<string | null>(null);
  const [renderedSvg, setRenderedSvg] = useState<string | null>(null);
  const diagramIdRef = useRef(`orchestration-graph-${diagramCounter++}`);
  const theme = useGraphTheme();

  useEffect(() => {
    let cancelled = false;
    api
      .getModeGraph(mode)
      .then((res) => {
        if (!cancelled) setSource(res.mermaid);
      })
      .catch(() => {
        if (!cancelled) setSource(null);
      });
    return () => {
      cancelled = true;
    };
  }, [mode]);

  useEffect(() => {
    if (!source) return;
    let cancelled = false;

    const active = new Set(activeNodeIds.map(toMermaidId));
    const done = new Set(doneNodeIds.map(toMermaidId));
    const error = new Set(errorNodeIds.map(toMermaidId));
    const classLines = extractNodeIds(source)
      .map((id) => `  class ${id} ${resolveClass(id, active, done, error)}`)
      .join("\n");

    const fullSource = [mermaidInit(theme), source, classDefs(theme), classLines].join("\n");

    import("mermaid").then(async ({ default: mermaid }) => {
      if (cancelled) return;
      mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
      try {
        const { svg } = await mermaid.render(diagramIdRef.current, fullSource);
        if (!cancelled) setRenderedSvg(svg);
      } catch {
        // Malformed source shouldn't happen against the backend's own
        // generated graphs — leave whatever rendered last in place rather
        // than crash the message around it.
      }
    });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, theme, activeNodeIds.join(","), doneNodeIds.join(","), errorNodeIds.join(",")]);

  if (!renderedSvg) return null;

  return (
    <div className="mt-2 max-w-xl overflow-x-auto rounded-lg border bg-card/60 p-3">
      <div dangerouslySetInnerHTML={{ __html: renderedSvg }} />
    </div>
  );
}
