"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Sparkles, ArrowUp, Loader2, User as UserIcon, Bot } from "lucide-react";
import { api, type AgentStep, type GroundingReport } from "@/lib/api";
import { cn } from "@/lib/utils";
import { AgentTimeline } from "@/components/chat/agent-timeline";
import { GroundingBadge } from "@/components/chat/grounding-badge";
import { RichMessage } from "@/components/chat/rich-message";

interface Msg {
  role: "user" | "assistant";
  content: string;
  steps?: AgentStep[];
  grounding?: GroundingReport;
}

const STARTERS = [
  "Find me wireless headphones under $300",
  "What are today's best deals?",
  "Compare the top-rated coffee makers",
  "Recommend a gift for a runner",
];

function Assistant() {
  const params = useSearchParams();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const sentInitial = useRef(false);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || streaming) return;
      setInput("");
      setMessages((m) => [...m, { role: "user", content: trimmed }, { role: "assistant", content: "", steps: [] }]);
      setStreaming(true);
      try {
        await api.chatStream(
          trimmed,
          undefined,
          (chunk) => {
            setMessages((m) => {
              const next = [...m];
              const last = next[next.length - 1];
              if (last?.role === "assistant") last.content += chunk;
              return next;
            });
          },
          undefined,
          {
            onStep: (step) => {
              setMessages((m) => {
                const next = [...m];
                const last = next[next.length - 1];
                if (last?.role === "assistant") last.steps = [...(last.steps ?? []), step];
                return next;
              });
            },
            onGrounding: (report) => {
              setMessages((m) => {
                const next = [...m];
                const last = next[next.length - 1];
                if (last?.role === "assistant") last.grounding = report;
                return next;
              });
            },
          },
        );
      } catch {
        setMessages((m) => {
          const next = [...m];
          const last = next[next.length - 1];
          if (last?.role === "assistant" && !last.content) {
            last.content = "Sorry — I couldn't reach the assistant. Please try again.";
          }
          return next;
        });
      } finally {
        setStreaming(false);
      }
    },
    [streaming],
  );

  // Auto-send a prompt passed via ?prompt= (e.g. from a product page).
  useEffect(() => {
    const prompt = params.get("prompt");
    if (prompt && !sentInitial.current) {
      sentInitial.current = true;
      void send(prompt);
    }
  }, [params, send]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  return (
    <div className="mx-auto flex h-[calc(100vh-3.5rem)] max-w-3xl flex-col px-4 sm:px-6">
      <div ref={scrollRef} className="flex-1 overflow-y-auto py-8">
        {messages.length === 0 ? (
          <div className="mx-auto max-w-xl py-10 text-center">
            <div className="mx-auto flex size-12 items-center justify-center rounded-2xl bg-primary/10">
              <Sparkles className="size-6 text-primary" />
            </div>
            <h1 className="mt-4 text-xl font-bold tracking-tight">Shopping assistant</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Describe what you want and the agents will search, compare, and recommend.
              Sign in for orders, tracking, and returns.
            </p>
            <div className="mt-6 grid gap-2 sm:grid-cols-2">
              {STARTERS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => send(s)}
                  className="rounded-lg border bg-card px-3 py-2 text-left text-sm transition-colors hover:border-primary/40 hover:bg-accent"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map((m, i) => (
              <div key={i} className={cn("flex gap-2.5", m.role === "user" ? "justify-end" : "justify-start")}>
                {m.role === "assistant" && (
                  <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
                    <Bot className="size-4" />
                  </div>
                )}
                <div
                  className={cn(
                    "flex max-w-[80%] flex-col gap-1",
                    m.role === "user" ? "items-end" : "items-start",
                  )}
                >
                  <div
                    className={cn(
                      "rounded-2xl px-4 py-2.5 text-sm",
                      m.role === "user"
                        ? "bg-primary text-primary-foreground"
                        : "bg-muted text-foreground",
                    )}
                  >
                    {m.role === "assistant" && !m.content ? (
                      <Loader2 className="size-4 animate-spin text-muted-foreground" />
                    ) : m.role === "assistant" ? (
                      <RichMessage
                        content={m.content}
                        streaming={streaming && i === messages.length - 1}
                        onAction={(text) => send(text)}
                      />
                    ) : (
                      m.content
                    )}
                  </div>
                  {m.role === "assistant" && m.steps && m.steps.length > 0 && (
                    <AgentTimeline steps={m.steps} />
                  )}
                  {m.role === "assistant" && <GroundingBadge report={m.grounding} />}
                </div>
                {m.role === "user" && (
                  <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
                    <UserIcon className="size-4" />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="mb-4"
      >
        <div className="flex items-end gap-2 rounded-2xl border bg-card p-2 shadow-sm">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            rows={1}
            placeholder="Ask about products…"
            className="max-h-32 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground"
          />
          <button
            type="submit"
            disabled={!input.trim() || streaming}
            aria-label="Send"
            className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {streaming ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
          </button>
        </div>
        <p className="mt-2 text-center text-xs text-muted-foreground">
          Discovery is open to everyone.{" "}
          <Link href="/login" className="text-primary hover:underline">Sign in</Link> for orders & returns.
        </p>
      </form>
    </div>
  );
}

export default function AssistantPage() {
  return (
    <Suspense fallback={<div className="mx-auto max-w-3xl px-4 py-8" />}>
      <Assistant />
    </Suspense>
  );
}
