"use client";

import React from "react";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { ArrowUp, Check, ChevronDown, Paperclip, Square, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils";

// ─── Tooltip ─────────────────────────────────────────────────────────────────

const TooltipProvider = TooltipPrimitive.Provider;
const Tooltip = TooltipPrimitive.Root;
const TooltipTrigger = TooltipPrimitive.Trigger;

const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 6, ...props }, ref) => (
  <TooltipPrimitive.Content
    ref={ref}
    sideOffset={sideOffset}
    className={cn(
      "z-50 rounded-md border border-border bg-popover px-2.5 py-1 text-xs text-popover-foreground shadow-sm",
      "animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
      className
    )}
    {...props}
  />
));
TooltipContent.displayName = "TooltipContent";

// ─── Image preview overlay ────────────────────────────────────────────────────

function ImagePreviewOverlay({ src, onClose }: { src: string; onClose: () => void }) {
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="relative max-w-[90vw]" onClick={(e) => e.stopPropagation()}>
        <img src={src} alt="Preview" className="max-h-[85vh] max-w-full rounded-xl object-contain shadow-2xl" />
        <button onClick={onClose} className="absolute -right-2 -top-2 rounded-full bg-zinc-800 p-1.5 text-white transition-colors hover:bg-zinc-700">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

// ─── Agent mode definitions ───────────────────────────────────────────────────

export interface AgentMode {
  id: string | null;
  label: string;
  placeholder: string;
}

export const AGENT_MODES: AgentMode[] = [
  { id: null, label: "Auto", placeholder: "Ask about products, orders, or anything..." },
  { id: "product-discovery", label: "Products", placeholder: "Search, compare, or explore the catalog..." },
  { id: "order-management", label: "Orders", placeholder: "Track, cancel, or return an order..." },
  { id: "pricing-promotions", label: "Pricing", placeholder: "Check deals, coupons, or price trends..." },
  { id: "review-sentiment", label: "Reviews", placeholder: "Analyse reviews or check sentiment..." },
  { id: "inventory-fulfillment", label: "Inventory", placeholder: "Check stock or estimate shipping..." },
];

// ─── PromptInputBox ───────────────────────────────────────────────────────────

export interface PromptInputBoxProps {
  onSend: (message: string, agentMode?: string | null) => void;
  isLoading?: boolean;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  suggestions?: { label: string; prompt: string }[];
}

export const PromptInputBox = React.forwardRef<HTMLDivElement, PromptInputBoxProps>(
  ({ onSend, isLoading = false, className, disabled = false, suggestions = [] }, ref) => {
    const [input, setInput] = React.useState("");
    const [agentMode, setAgentMode] = React.useState<string | null>(null);
    const [attachedFile, setAttachedFile] = React.useState<File | null>(null);
    const [filePreview, setFilePreview] = React.useState<string | null>(null);
    const [selectedImage, setSelectedImage] = React.useState<string | null>(null);
    const [modeMenuOpen, setModeMenuOpen] = React.useState(false);
    const modeMenuRef = React.useRef<HTMLDivElement>(null);
    const textareaRef = React.useRef<HTMLTextAreaElement>(null);
    const fileInputRef = React.useRef<HTMLInputElement>(null);
    const MAX_HEIGHT = 240;

    const currentMode = AGENT_MODES.find((m) => m.id === agentMode) ?? AGENT_MODES[0];
    const placeholder = currentMode.placeholder;

    // Auto-resize textarea
    React.useEffect(() => {
      const ta = textareaRef.current;
      if (!ta) return;
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, MAX_HEIGHT)}px`;
    }, [input]);

    // Close the mode menu on an outside click or Escape. A popover that only
    // closes by re-clicking its trigger traps a user who opened it by accident,
    // which is the most likely way anyone opens this one.
    React.useEffect(() => {
      if (!modeMenuOpen) return;
      const onPointerDown = (e: PointerEvent) => {
        if (!modeMenuRef.current?.contains(e.target as Node)) setModeMenuOpen(false);
      };
      const onKeyDown = (e: KeyboardEvent) => {
        if (e.key === "Escape") setModeMenuOpen(false);
      };
      document.addEventListener("pointerdown", onPointerDown);
      document.addEventListener("keydown", onKeyDown);
      return () => {
        document.removeEventListener("pointerdown", onPointerDown);
        document.removeEventListener("keydown", onKeyDown);
      };
    }, [modeMenuOpen]);

    const isDisabled = disabled || isLoading;
    const hasContent = input.trim() !== "" || attachedFile !== null;

    const handleSubmit = () => {
      if (!hasContent || isDisabled) return;
      onSend(input.trim(), agentMode);
      setInput("");
      setAttachedFile(null);
      setFilePreview(null);
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    };

    const attachFile = (file: File) => {
      if (!file.type.startsWith("image/") || file.size > 10 * 1024 * 1024) return;
      setAttachedFile(file);
      const reader = new FileReader();
      reader.onload = (ev) => setFilePreview(ev.target?.result as string);
      reader.readAsDataURL(file);
    };

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) attachFile(file);
      e.target.value = "";
    };

    const removeFile = () => { setAttachedFile(null); setFilePreview(null); };

    // Paste image
    React.useEffect(() => {
      const handler = (e: ClipboardEvent) => {
        const items = e.clipboardData?.items;
        if (!items) return;
        for (const item of Array.from(items)) {
          if (item.type.startsWith("image/")) {
            const file = item.getAsFile();
            if (file) { e.preventDefault(); attachFile(file); }
            break;
          }
        }
      };
      document.addEventListener("paste", handler);
      return () => document.removeEventListener("paste", handler);
    }, []);

    const handleDrop = (e: React.DragEvent) => {
      e.preventDefault();
      const file = Array.from(e.dataTransfer.files).find((f) => f.type.startsWith("image/"));
      if (file) attachFile(file);
    };

    return (
      <>
        <TooltipProvider>
          <div
            ref={ref}
            className={cn(
              "rounded-2xl border bg-background shadow-sm transition-colors duration-200",
              isLoading ? "border-teal-400/60" : "border-border hover:border-zinc-300",
              className
            )}
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleDrop}
          >
            {/* Attached image thumbnail */}
            <AnimatePresence>
              {filePreview && attachedFile && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.18 }}
                  className="overflow-hidden px-3 pt-2"
                >
                  <div className="relative inline-block">
                    <button type="button" className="block h-16 w-16 overflow-hidden rounded-lg" onClick={() => setSelectedImage(filePreview)}>
                      <img src={filePreview} alt={attachedFile.name} className="h-full w-full object-cover" />
                    </button>
                    <button type="button" onClick={removeFile} className="absolute -right-1.5 -top-1.5 rounded-full bg-zinc-700 p-0.5 text-white transition-colors hover:bg-zinc-600">
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Textarea */}
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={placeholder}
              disabled={isDisabled}
              rows={1}
              className={cn(
                "w-full resize-none bg-transparent px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground",
                "focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-60",
                "min-h-[44px] overflow-y-auto leading-relaxed"
              )}
              style={{ maxHeight: MAX_HEIGHT, scrollbarWidth: "thin" }}
            />

            {/* Suggested prompts — shown when input is empty */}
            <AnimatePresence>
              {!input && suggestions.length > 0 && !isLoading && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.15 }}
                  className="overflow-hidden px-3 pb-1"
                >
                  <div className="flex flex-wrap gap-1.5">
                    {suggestions.slice(0, 3).map((s) => (
                      <button
                        key={s.label}
                        type="button"
                        onClick={() => { setInput(s.prompt); textareaRef.current?.focus(); }}
                        className="rounded-full border border-border/60 bg-muted/40 px-2.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-primary/30 hover:bg-muted hover:text-foreground"
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Bottom actions */}
            <div className="flex items-center justify-between px-3 pb-3">
              {/* Attach button */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isDisabled}
                    className="flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
                  >
                    <Paperclip className="h-4 w-4" />
                  </button>
                </TooltipTrigger>
                <TooltipContent>Attach image</TooltipContent>
              </Tooltip>

              <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleFileChange} />

              {/*
                Specialist picker, collapsed.

                This was a six-chip row pinned above the textarea on every
                message. `Auto` is the default and the one most people want —
                it is what lets the orchestrator route for you — so the row spent
                a full line of composer height advertising five options that are
                rarely the right answer, on every viewport.

                Collapsed to a single button that names the ACTIVE mode, so the
                current state is still visible at a glance while costing a
                button rather than a row. Only shown as "Auto" when nothing is
                pinned; picking a specialist makes the button read that
                specialist, which is the state worth surfacing.

                NOT the orchestration-mode switcher. That is a different control
                (`chat/mode-switcher.tsx`, fed by GET /api/orchestration/modes)
                on the page toolbar, and issue #4 puts it explicitly out of
                scope. Collapsing that one would hide the feature this repo is
                built around.
              */}
              <div className="relative" ref={modeMenuRef}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={() => setModeMenuOpen((v) => !v)}
                      disabled={isDisabled}
                      aria-haspopup="listbox"
                      aria-expanded={modeMenuOpen}
                      aria-label="Specialist"
                      className={cn(
                        "flex h-8 items-center gap-1 rounded-full px-2.5 text-[11px] font-medium transition-colors disabled:pointer-events-none disabled:opacity-40",
                        agentMode === null
                          ? "text-muted-foreground hover:bg-muted hover:text-foreground"
                          : "bg-primary text-primary-foreground hover:bg-primary/90"
                      )}
                    >
                      {currentMode.label}
                      <ChevronDown className={cn("h-3 w-3 transition-transform", modeMenuOpen && "rotate-180")} />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent>
                    {agentMode === null ? "Route automatically" : `Pinned to ${currentMode.label}`}
                  </TooltipContent>
                </Tooltip>

                <AnimatePresence>
                  {modeMenuOpen && (
                    <motion.div
                      role="listbox"
                      aria-label="Specialist"
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: 4 }}
                      transition={{ duration: 0.12 }}
                      className="absolute bottom-full left-0 z-50 mb-2 min-w-44 overflow-hidden rounded-lg border bg-popover p-1 shadow-md"
                    >
                      {AGENT_MODES.map((mode) => (
                        <button
                          key={String(mode.id)}
                          type="button"
                          role="option"
                          aria-selected={agentMode === mode.id}
                          onClick={() => {
                            setAgentMode(mode.id);
                            setModeMenuOpen(false);
                            textareaRef.current?.focus();
                          }}
                          className={cn(
                            "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors",
                            agentMode === mode.id
                              ? "bg-accent text-accent-foreground"
                              : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                          )}
                        >
                          {mode.label}
                          {agentMode === mode.id && <Check className="h-3 w-3 shrink-0" />}
                        </button>
                      ))}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>

              {/* Keyboard hint */}
              <span className="hidden select-none text-[10px] text-muted-foreground/50 sm:block">
                Enter to send · Shift+Enter newline
              </span>

              {/* Send / Stop button */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <motion.button
                    type="button"
                    onClick={handleSubmit}
                    disabled={!hasContent && !isLoading}
                    whileTap={{ scale: 0.88 }}
                    transition={{ type: "spring", stiffness: 400, damping: 17 }}
                    className={cn(
                      "flex h-8 w-8 items-center justify-center rounded-full transition-all duration-200",
                      isLoading
                        ? "bg-red-500 text-white hover:bg-red-600"
                        : hasContent
                          ? "bg-teal-600 text-white hover:bg-teal-700"
                          : "bg-muted text-muted-foreground"
                    )}
                  >
                    <AnimatePresence mode="wait" initial={false}>
                      {isLoading ? (
                        <motion.span key="stop" initial={{ opacity: 0, scale: 0.7 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.7 }} transition={{ duration: 0.12 }}>
                          <Square className="h-3.5 w-3.5 fill-white" />
                        </motion.span>
                      ) : (
                        <motion.span key="send" initial={{ opacity: 0, scale: 0.7, y: 4 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.7, y: -4 }} transition={{ duration: 0.12 }}>
                          <ArrowUp className="h-4 w-4" />
                        </motion.span>
                      )}
                    </AnimatePresence>
                  </motion.button>
                </TooltipTrigger>
                <TooltipContent>{isLoading ? "Stop generation" : "Send message"}</TooltipContent>
              </Tooltip>
            </div>
          </div>
        </TooltipProvider>

        {selectedImage && <ImagePreviewOverlay src={selectedImage} onClose={() => setSelectedImage(null)} />}
      </>
    );
  }
);

PromptInputBox.displayName = "PromptInputBox";
