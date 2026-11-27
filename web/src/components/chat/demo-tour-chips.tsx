"use client";

import { useEffect, useRef, useState } from "react";
import { Info, RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useDemoTour, type DemoTourChip } from "@/lib/demo-tour";
import { Button } from "@/components/ui/button";
import { PREVIEW, QUICKSTART_URL } from "@/lib/preview/config";

interface DemoTourChipsProps {
  /** Send the chip's prompt, in its mode, in a new or the current conversation. */
  onPick: (chip: DemoTourChip) => void;
  disabled?: boolean;
  /** Show only the first N chips (the replay-miss notice keeps to a few). */
  limit?: number;
  /** Left-align the chips and drop the heading, for use inside another notice. */
  compact?: boolean;
}

const CHIP_CLASS =
  "rounded-full border border-primary/30 bg-card px-3 py-1.5 text-sm text-foreground/80 transition-colors hover:border-primary/60 hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50";

/**
 * The demo tour's suggested prompts. Renders nothing unless the backend serves a
 * tour (`GET /api/demo/tour` answers 404 on a normal install), so it is safe to
 * mount unconditionally.
 */
export function DemoTourChips({ onPick, disabled, limit, compact }: DemoTourChipsProps) {
  const tour = useDemoTour();
  if (!tour) return null;
  const chips = limit ? tour.chips.slice(0, limit) : tour.chips;

  return (
    <div className={compact ? "flex flex-col gap-2" : "flex flex-col items-center gap-2"} data-testid="demo-tour-chips">
      {!compact && <p className="text-xs font-medium text-muted-foreground">Guided demo: recorded answers</p>}
      <div className={compact ? "flex flex-wrap gap-2" : "flex flex-wrap justify-center gap-2"}>
        {chips.map((chip) => (
          <button
            key={chip.id}
            type="button"
            disabled={disabled}
            title={chip.prompt}
            onClick={() => onPick(chip)}
            className={CHIP_CLASS}
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

/**
 * Shown in the thread, in place of an answer, when the demo has no recording
 * for a prompt. Styled as a notice rather than an assistant reply: it is the
 * demo talking, not the assistant.
 */
export function ReplayMissCallout({ message, onPick, disabled }: ReplayMissCalloutProps) {
  const tour = useDemoTour();
  const text = message || tour?.miss_message || "";
  const ref = useRef<HTMLDivElement>(null);

  // The chips arrive with the manifest, after the thread has already scrolled
  // to the bottom; scroll again so the notice is not left under the composer.
  useEffect(() => {
    ref.current?.scrollIntoView?.({ block: "end", behavior: "smooth" });
  }, [tour]);

  return (
    <div
      ref={ref}
      role="status"
      className="w-full max-w-lg space-y-3 rounded-lg border border-info/30 bg-info/5 p-3"
      data-testid="replay-miss-callout"
    >
      <div className="flex gap-2">
        <Info className="mt-0.5 size-4 shrink-0 text-info" aria-hidden />
        <div className="space-y-1">
          <p className="text-sm font-medium text-foreground">Not part of the recorded demo</p>
          <p className="text-sm text-muted-foreground">
            {text}
            {PREVIEW && (
              <>
                {" "}
                <a href={QUICKSTART_URL} className="font-medium text-foreground underline underline-offset-2" target="_blank" rel="noreferrer">
                  Quick start
                </a>
              </>
            )}
          </p>
        </div>
      </div>
      <div className="pl-6">
        <DemoTourChips onPick={onPick} disabled={disabled} limit={4} compact />
      </div>
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
