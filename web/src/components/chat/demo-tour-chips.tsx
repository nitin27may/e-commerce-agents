"use client";

import { useState } from "react";
import { RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useDemoTour, type DemoTourChip } from "@/lib/demo-tour";
import { Button } from "@/components/ui/button";

interface DemoTourChipsProps {
  /** Send the chip's prompt, in its mode, in a new or the current conversation. */
  onPick: (chip: DemoTourChip) => void;
  disabled?: boolean;
}

/**
 * The demo tour's suggested prompts. Renders nothing unless the backend serves a
 * tour (`GET /api/demo/tour` answers 404 on a normal install), so it is safe to
 * mount unconditionally.
 */
export function DemoTourChips({ onPick, disabled }: DemoTourChipsProps) {
  const tour = useDemoTour();
  if (!tour) return null;

  return (
    <div className="flex flex-col items-center gap-2" data-testid="demo-tour-chips">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Guided demo - recorded answers
      </p>
      <div className="flex flex-wrap justify-center gap-2">
        {tour.chips.map((chip) => (
          <button
            key={chip.id}
            type="button"
            disabled={disabled}
            title={chip.prompt}
            onClick={() => onPick(chip)}
            className="rounded-full border border-primary/30 bg-card px-3 py-1.5 text-sm text-foreground/80 transition-colors hover:border-primary/60 hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
          >
            {chip.label}
          </button>
        ))}
      </div>
    </div>
  );
}

interface ReplayMissCalloutProps {
  /** The message the server sent with the miss; falls back to the manifest's. */
  message: string;
  onPick: (chip: DemoTourChip) => void;
  disabled?: boolean;
}

/** Shown in the thread, in place of an answer, when the demo has no recording for a prompt. */
export function ReplayMissCallout({ message, onPick, disabled }: ReplayMissCalloutProps) {
  const tour = useDemoTour();
  const text = message || tour?.miss_message || "";

  return (
    <div role="status" className="space-y-3" data-testid="replay-miss-callout">
      <p className="text-sm">{text}</p>
      <DemoTourChips onPick={onPick} disabled={disabled} />
    </div>
  );
}

interface DemoResetButtonProps {
  /** Called after the server has reset the tour user's data. */
  onReset: () => void;
  disabled?: boolean;
}

/** "Reset demo": offered only to the tour user, in a backend that serves a tour. */
export function DemoResetButton({ onReset, disabled }: DemoResetButtonProps) {
  const tour = useDemoTour();
  const { user } = useAuth();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  if (!tour || !user || user.email.toLowerCase() !== tour.tour_user.toLowerCase()) return null;

  async function reset() {
    setBusy(true);
    setFailed(false);
    try {
      await api.resetDemo();
      onReset();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Button type="button" variant="ghost" size="sm" disabled={disabled || busy} onClick={reset}>
        <RotateCcw className="size-3.5" />
        Reset demo
      </Button>
      {failed && <span className="text-xs text-destructive">Reset failed</span>}
    </div>
  );
}
