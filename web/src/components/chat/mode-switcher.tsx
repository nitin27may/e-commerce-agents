"use client";

import { useEffect, useState } from "react";
import { api, type OrchestrationMode } from "@/lib/api";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";

interface ModeSwitcherProps {
  /** An `OrchestrationMode.name` (e.g. "tool", "workflow:pre-purchase"), or "" for the server default. */
  value: string;
  onChange: (mode: string) => void;
  disabled?: boolean;
}

/**
 * Composer control for picking which orchestration mode runs the next
 * turn — fed by `GET /api/orchestration/modes`. This is what makes the
 * capstone's flagship claim demonstrable in the UI: the same domain, run
 * through the plain LLM tool router, MAF's HandoffBuilder mesh, or a
 * fixed workflow graph, picked per message.
 *
 * Fails soft: if the modes fetch errors (or returns an empty list — e.g.
 * an older backend without the registry), the control renders nothing
 * and chat keeps working through the server's default mode.
 */
export function ModeSwitcher({ value, onChange, disabled }: ModeSwitcherProps) {
  const [modes, setModes] = useState<OrchestrationMode[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getOrchestrationModes()
      .then((data) => {
        if (!cancelled) setModes(data);
      })
      .catch(() => {
        // Modes endpoint unreachable — chat still works via the
        // backend's own default, so this fails silent rather than
        // surfacing an error banner for a non-essential control.
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loaded && modes.length === 0) return null;

  const active = modes.find((m) => m.name === value);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        // Always pass a string, never undefined — verified live that
        // toggling between the two mid-lifecycle (value="" -> undefined on
        // first render, then a real string once selected) makes base-ui log
        // "changing the uncontrolled value state ... to be controlled" and
        // is the same controlled/uncontrolled footgun React warns about for
        // plain inputs. "" reads as "no selection" fine on its own — it's
        // the type flip-flop that broke, not the empty value.
        value={value}
        onValueChange={(v) => v && onChange(v)}
        disabled={disabled || modes.length === 0}
      >
        <SelectTrigger size="sm" className="w-auto min-w-40" aria-label="Orchestration mode">
          {/* base-ui's SelectValue ignores `placeholder` once `children` is a
              function — verified live (an empty value rendered a blank
              trigger, not "Mode") — so the empty case has to be handled here. */}
          <SelectValue placeholder="Mode">
            {(v: string) => (v ? (modes.find((m) => m.name === v)?.label ?? v) : "Mode")}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          {modes.map((mode) => (
            <SelectItem key={mode.name} value={mode.name}>
              {mode.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {active && (
        <div className="flex flex-wrap gap-1" title={active.description}>
          {active.capabilities.is_graph && (
            <Badge variant="outline" className="text-[10px] font-normal">
              graph
            </Badge>
          )}
          {active.capabilities.supports_hitl && (
            <Badge variant="outline" className="text-[10px] font-normal">
              HITL
            </Badge>
          )}
          {active.capabilities.supports_checkpoints && (
            <Badge variant="outline" className="text-[10px] font-normal">
              checkpoints
            </Badge>
          )}
        </div>
      )}
    </div>
  );
}
