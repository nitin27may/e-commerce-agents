"use client";

import {
  useState,
  useEffect,
  useRef,
  useCallback,
} from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { useCart } from "@/lib/cart-context";
import { api, type AgentStep } from "@/lib/api";
import { RichMessage } from "@/components/chat/rich-message";
import { AgentTimeline } from "@/components/chat/agent-timeline";
import { OrchestrationGraph } from "@/components/chat/orchestration-graph";
import { ModeSwitcher } from "@/components/chat/mode-switcher";
import { QUICK_PROMPTS } from "@/lib/scenarios";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PromptInputBox } from "@/components/ui/ai-prompt-box";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  Sheet,
  SheetTrigger,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import {
  MessageSquarePlusIcon,
  Trash2Icon,
  PanelLeftIcon,
  BotIcon,
  UserIcon,
  ShoppingCart,
  Copy,
  Check,
  RotateCcw,
  Share2,
  Sparkles,
} from "lucide-react";
import { DEMO_SCENARIOS } from "@/lib/scenarios";
import { AGENT_MODES } from "@/components/ui/ai-prompt-box";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  agents_involved?: string[];
  steps?: AgentStep[];
  created_at?: string;
  streaming?: boolean;
  /** Orchestration mode this turn ran through — set on the assistant
   * message so OrchestrationGraph knows which mode's graph to render,
   * independent of whatever's currently selected in the switcher. */
  mode?: string;
  /** Executor ids (dashed, live form) currently mid-run / completed / errored — from `event: node`/`error` SSE frames. */
  activeNodeIds?: string[];
  doneNodeIds?: string[];
  errorNodeIds?: string[];
}

interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Orchestration mode persistence — remembered per conversation (and for a
// not-yet-created conversation, under a "draft" key) so a reload keeps the
// same mode selected. Distinct from AGENT_MODES/agentMode in
// ai-prompt-box.tsx, which pick a specialist to route to directly — this
// picks how the orchestrator itself runs the turn.
// ---------------------------------------------------------------------------

const ORCH_MODE_STORAGE_PREFIX = "ecommerce_orch_mode:";
const ORCH_MODE_DRAFT_KEY = "draft";

function loadStoredOrchestrationMode(conversationId: string | null): string {
  if (typeof window === "undefined") return "";
  try {
    return localStorage.getItem(ORCH_MODE_STORAGE_PREFIX + (conversationId ?? ORCH_MODE_DRAFT_KEY)) ?? "";
  } catch {
    return "";
  }
}

function storeOrchestrationMode(conversationId: string | null, mode: string): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(ORCH_MODE_STORAGE_PREFIX + (conversationId ?? ORCH_MODE_DRAFT_KEY), mode);
  } catch {
    // Private browsing / storage full — the mode still works for this
    // session via component state, it just won't survive a reload.
  }
}

// ---------------------------------------------------------------------------
// Thinking indicator (replaces generic typing dots)
// ---------------------------------------------------------------------------

function ThinkingIndicator({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 px-1 py-3">
      <Avatar className="size-7 shrink-0">
        <AvatarFallback className="bg-muted text-muted-foreground text-xs">
          <BotIcon className="size-3.5" />
        </AvatarFallback>
      </Avatar>
      <motion.div
        className="flex items-center gap-1.5 text-xs text-muted-foreground"
        animate={{ opacity: [0.5, 1, 0.5] }}
        transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
      >
        <Sparkles className="size-3 text-primary/70" />
        <span>{label}</span>
      </motion.div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Message action bar (copy, retry, share)
// ---------------------------------------------------------------------------

function MessageActions({
  text,
  onRetry,
}: {
  text: string;
  onRetry: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [shared, setShared] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard unavailable */ }
  };

  const handleShare = async () => {
    try {
      const url = `${window.location.origin}/chat?prompt=${encodeURIComponent(text.slice(0, 200))}`;
      await navigator.clipboard.writeText(url);
      setShared(true);
      setTimeout(() => setShared(false), 1500);
    } catch { /* clipboard unavailable */ }
  };

  return (
    <div className="mt-1.5 flex items-center gap-3">
      <button
        type="button"
        onClick={handleCopy}
        className="inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
      >
        {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
        {copied ? "Copied" : "Copy"}
      </button>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
      >
        <RotateCcw className="size-3" />
        Retry
      </button>
      <button
        type="button"
        onClick={handleShare}
        className="inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
      >
        {shared ? <Check className="size-3" /> : <Share2 className="size-3" />}
        {shared ? "Copied link" : "Share"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Conversation list (shared between desktop panel and mobile sheet)
// ---------------------------------------------------------------------------

function ConversationList({
  conversations,
  activeId,
  onSelect,
  onDelete,
  onNew,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-3 py-2.5">
        <span className="text-sm font-semibold text-foreground">
          Conversations
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={onNew}
          title="New chat"
        >
          <MessageSquarePlusIcon className="size-4" />
        </Button>
      </div>
      <Separator />
      <ScrollArea className="flex-1 overflow-y-auto">
        <div className="flex flex-col gap-0.5 p-1.5">
          {conversations.length === 0 && (
            <p className="px-2 py-8 text-center text-xs text-muted-foreground">
              No conversations yet. Send a message to start one.
            </p>
          )}
          {conversations.map((conv) => (
            <div
              key={conv.id}
              className={`group flex items-center gap-1 rounded-md px-2.5 py-1.5 text-sm cursor-pointer transition-colors ${
                conv.id === activeId
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              }`}
              onClick={() => onSelect(conv.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onSelect(conv.id);
              }}
              role="button"
              tabIndex={0}
            >
              <span className="flex-1 truncate">{conv.title}</span>
              <Button
                variant="ghost"
                size="icon-xs"
                className="opacity-0 group-hover:opacity-100 transition-opacity shrink-0 text-muted-foreground hover:text-destructive"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(conv.id);
                }}
                title="Delete conversation"
              >
                <Trash2Icon className="size-3" />
              </Button>
            </div>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Message content renderer
// ---------------------------------------------------------------------------

function MessageContent({ content }: { content: string }) {
  const paragraphs = content.split("\n\n");
  if (paragraphs.length <= 1) {
    return <span className="whitespace-pre-wrap">{content}</span>;
  }
  return (
    <>
      {paragraphs.map((p, i) => (
        <p
          key={i}
          className={i > 0 ? "mt-2 whitespace-pre-wrap" : "whitespace-pre-wrap"}
        >
          {p}
        </p>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// Chat Page
// ---------------------------------------------------------------------------

export default function ChatPage() {
  const { isAuthenticated } = useAuth();
  const { itemCount } = useCart();

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isResponding, setIsResponding] = useState(false);
  const [thinkingLabel, setThinkingLabel] = useState<string>("Routing to specialists...");
  const [sheetOpen, setSheetOpen] = useState(false);
  const [orchestrationMode, setOrchestrationMode] = useState<string>("");

  const searchParams = useSearchParams();
  const pendingQueryRef = useRef<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  // Per-message abort controller. New send aborts any in-flight stream;
  // unmount aborts whatever's still running. Plugs the SSE-leak finding.
  const streamAbortRef = useRef<AbortController | null>(null);
  // Set by sendMessage() right after it creates a new conversation, so the
  // "load messages when active conversation changes" effect below can skip
  // its usual server reload for that one transition — see that effect's
  // comment.
  const justCreatedConversationRef = useRef<string | null>(null);

  // Cancel in-flight stream when the component unmounts (route change).
  useEffect(
    () => () => {
      streamAbortRef.current?.abort();
    },
    []
  );

  // ---- Load conversations on mount ----
  useEffect(() => {
    if (!isAuthenticated) return;
    loadConversations();
  }, [isAuthenticated]);

  // ---- Auto-send ?q= query param on mount ----
  useEffect(() => {
    if (!isAuthenticated) return;
    // `prompt` is the deep-link param from home quick-prompts / product pages;
    // `q` is kept for back-compat.
    const q = searchParams.get("prompt") ?? searchParams.get("q");
    if (q) {
      pendingQueryRef.current = q;
    }
  }, [isAuthenticated, searchParams]);

  // Fire the pending query once conversations have loaded (initial mount)
  useEffect(() => {
    if (pendingQueryRef.current && !isResponding) {
      const q = pendingQueryRef.current;
      pendingQueryRef.current = null;
      sendMessage(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversations]);

  async function loadConversations() {
    try {
      const data = await api.getConversations();
      setConversations(data);
    } catch {
      // Silently handle -- empty list is fine on first load
    }
  }

  // ---- Load messages when active conversation changes ----
  useEffect(() => {
    if (!activeConversationId) {
      setMessages([]);
      return;
    }
    if (justCreatedConversationRef.current === activeConversationId) {
      // sendMessage() just set this id after creating the conversation for
      // its own first turn — local `messages` state is already complete
      // (and richer: it carries client-only fields like an assistant
      // message's `mode`/`activeNodeIds` that OrchestrationGraph needs and
      // the server has nowhere to persist). Reloading from the server here
      // would silently replace those messages and wipe those fields —
      // found live, as the graph rendering then disappearing a few hundred
      // ms after every first send in a new conversation.
      justCreatedConversationRef.current = null;
      return;
    }
    loadMessages(activeConversationId);
  }, [activeConversationId]);

  // ---- Restore the orchestration mode remembered for this conversation ----
  useEffect(() => {
    setOrchestrationMode(loadStoredOrchestrationMode(activeConversationId));
  }, [activeConversationId]);

  const handleModeChange = useCallback(
    (mode: string) => {
      setOrchestrationMode(mode);
      storeOrchestrationMode(activeConversationId, mode);
    },
    [activeConversationId],
  );

  async function loadMessages(conversationId: string) {
    try {
      const data = await api.getConversation(conversationId);
      setMessages(data.messages ?? []);
    } catch {
      setMessages([]);
    }
  }

  // ---- Auto-scroll to bottom ----
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isResponding]);

  // ---- Start new conversation ----
  const handleNewChat = useCallback(() => {
    setActiveConversationId(null);
    setMessages([]);
    setSheetOpen(false);
  }, []);

  // ---- Select conversation ----
  const handleSelectConversation = useCallback((id: string) => {
    setActiveConversationId(id);
    setSheetOpen(false);
  }, []);

  // ---- Delete conversation ----
  const handleDeleteConversation = useCallback(
    async (id: string) => {
      try {
        await api.deleteConversation(id);
        setConversations((prev) => prev.filter((c) => c.id !== id));
        if (activeConversationId === id) {
          setActiveConversationId(null);
          setMessages([]);
        }
      } catch {
        // Swallow -- UI stays consistent regardless
      }
    },
    [activeConversationId],
  );

  // ---- Core send logic (shared by form submit and ?q= auto-send) ----
  async function sendMessage(text: string, agentMode?: string | null) {
    if (!text.trim() || isResponding) return;

    const trimmed = text.trim();

    // Set initial thinking label from the selected mode
    const modeEntry = AGENT_MODES.find((m) => m.id === agentMode);
    setThinkingLabel(
      modeEntry && modeEntry.id
        ? `Routing to ${modeEntry.label}...`
        : "Routing to specialists..."
    );

    // Optimistic user message
    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: trimmed,
    };
    setMessages((prev) => [...prev, userMessage]);
    setIsResponding(true);

    const assistantId = crypto.randomUUID();
    let assistantCreated = false;
    // Snapshot now — orchestrationMode may change before this turn resolves
    // (e.g. the user picks a different mode for their next message while
    // this one is still streaming), but this message ran through whatever
    // was selected at send time.
    const messageMode = orchestrationMode || "tool";

    // Abort any prior stream still draining before starting a new one.
    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;

    try {
      const meta = await api.chatStream(
        trimmed,
        activeConversationId ?? undefined,
        (chunk) => {
          if (!assistantCreated) {
            assistantCreated = true;
            setMessages((prev) => [
              ...prev,
              {
                id: assistantId,
                role: "assistant",
                content: chunk,
                streaming: true,
                mode: messageMode,
              },
            ]);
          } else {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: m.content + chunk }
                  : m,
              ),
            );
          }
        },
        controller.signal,
        {
          onStep: (step) => {
            // Update thinking label with the active agent
            const agent = step.agent ?? "orchestrator";
            const AGENT_LABELS: Record<string, string> = {
              orchestrator: "Routing...",
              "product-discovery": "Product Discovery is searching...",
              "order-management": "Order Management is looking up...",
              "pricing-promotions": "Pricing is calculating...",
              "review-sentiment": "Reviews is analysing...",
              "inventory-fulfillment": "Inventory is checking stock...",
            };
            setThinkingLabel(AGENT_LABELS[agent] ?? `${agent} is working...`);

            setMessages((prev) => {
              if (!prev.some((m) => m.id === assistantId)) {
                assistantCreated = true;
                return [
                  ...prev,
                  {
                    id: assistantId,
                    role: "assistant",
                    content: "",
                    streaming: true,
                    steps: [step],
                    mode: messageMode,
                  },
                ];
              }
              return prev.map((m) =>
                m.id === assistantId
                  ? { ...m, steps: [...(m.steps ?? []), step] }
                  : m,
              );
            });
          },
          onOrchestrationEvent: (eventName, data) => {
            if (eventName !== "node" && eventName !== "error") return;
            const frame = data as { node_id?: string; phase?: "enter" | "exit" };
            const nodeId = frame.node_id;
            if (!nodeId) return;

            setMessages((prev) => {
              const existing = prev.find((m) => m.id === assistantId);
              const base: Message = existing ?? {
                id: assistantId,
                role: "assistant",
                content: "",
                streaming: true,
                mode: messageMode,
              };
              if (!existing) assistantCreated = true;

              let next: Message;
              if (eventName === "error") {
                next = { ...base, errorNodeIds: [...(base.errorNodeIds ?? []), nodeId] };
              } else if (frame.phase === "exit") {
                next = {
                  ...base,
                  activeNodeIds: (base.activeNodeIds ?? []).filter((id) => id !== nodeId),
                  doneNodeIds: [...(base.doneNodeIds ?? []), nodeId],
                };
              } else {
                next = { ...base, activeNodeIds: [...(base.activeNodeIds ?? []), nodeId] };
              }

              return existing ? prev.map((m) => (m.id === assistantId ? next : m)) : [...prev, next];
            });
          },
          mode: orchestrationMode || undefined,
        },
      );

      // If this was the first message, a new conversation was created —
      // re-key the draft-stored mode under the real conversation id so a
      // reload still picks it up.
      if (!activeConversationId && meta.conversation_id) {
        storeOrchestrationMode(meta.conversation_id, orchestrationMode);
        justCreatedConversationRef.current = meta.conversation_id;
        setActiveConversationId(meta.conversation_id);
        await loadConversations();
      }

      // Finalize: drop streaming flag, attach agents_involved
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, streaming: false, agents_involved: meta.agents_involved }
            : m,
        ),
      );
    } catch (err) {
      const errMsg =
        err instanceof Error ? err.message : "Something went wrong.";
      setMessages((prev) => {
        const existing = prev.find((m) => m.id === assistantId);
        if (existing) {
          return prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: `Error: ${errMsg}`, streaming: false }
              : m,
          );
        }
        return [
          ...prev,
          { id: assistantId, role: "assistant", content: `Error: ${errMsg}` },
        ];
      });
    } finally {
      setIsResponding(false);
    }
  }

  // ---- Render ----
  return (
    <div className="flex h-full">
      {/* -------- Conversation list panel (desktop) -------- */}
      <aside className="hidden w-60 shrink-0 flex-col border-r bg-muted/30 lg:flex">
        <ConversationList
          conversations={conversations}
          activeId={activeConversationId}
          onSelect={handleSelectConversation}
          onDelete={handleDeleteConversation}
          onNew={handleNewChat}
        />
      </aside>

      {/* -------- Main chat area -------- */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* ---- Top bar ---- */}
        <div className="flex h-11 items-center gap-2 border-b px-3">
          {/* Mobile conversation list toggle */}
          <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
            <SheetTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="lg:hidden"
                />
              }
            >
              <PanelLeftIcon className="size-4" />
            </SheetTrigger>
            <SheetContent side="left" className="w-72 p-0">
              <SheetHeader className="sr-only">
                <SheetTitle>Conversations</SheetTitle>
              </SheetHeader>
              <ConversationList
                conversations={conversations}
                activeId={activeConversationId}
                onSelect={handleSelectConversation}
                onDelete={handleDeleteConversation}
                onNew={handleNewChat}
              />
            </SheetContent>
          </Sheet>

          <h2 className="flex-1 truncate text-sm font-medium">
            {activeConversationId
              ? conversations.find((c) => c.id === activeConversationId)
                  ?.title ?? "Chat"
              : "New chat"}
          </h2>

          <Link
            href="/cart"
            className="relative flex items-center gap-1 rounded-lg px-2 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <ShoppingCart className="size-4" />
            {itemCount > 0 && (
              <span className="absolute -right-1 -top-1 flex size-4 items-center justify-center rounded-full bg-primary text-[9px] font-bold text-primary-foreground">
                {itemCount > 99 ? "99+" : itemCount}
              </span>
            )}
          </Link>

          <Button
            variant="ghost"
            size="icon-sm"
            className="lg:hidden"
            onClick={handleNewChat}
            title="New chat"
          >
            <MessageSquarePlusIcon className="size-4" />
          </Button>
        </div>

        {/* ---- Messages ---- */}
        <div className="flex-1 overflow-y-auto">
          {messages.length === 0 && !isResponding ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
              <div className="flex size-14 items-center justify-center rounded-full bg-primary/10">
                <BotIcon className="size-7 text-primary" />
              </div>
              <h3 className="text-base font-semibold">
                How can I help you today?
              </h3>
              <p className="max-w-sm text-sm text-muted-foreground">
                Ask about products, orders, pricing, reviews, inventory, or
                anything else. Our specialist agents will collaborate to help
                you.
              </p>
              <div className="mt-2 flex flex-wrap justify-center gap-2">
                {QUICK_PROMPTS.map((s) => (
                  <button
                    key={s.label}
                    type="button"
                    onClick={() => sendMessage(s.prompt)}
                    className="rounded-full border bg-card px-3 py-1.5 text-sm text-foreground/80 transition-colors hover:border-primary/40 hover:bg-accent hover:text-foreground"
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl px-4 py-4">
              {messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`mb-4 flex items-start gap-2.5 ${
                    msg.role === "user" ? "flex-row-reverse" : "flex-row"
                  }`}
                >
                  <Avatar className="mt-0.5 size-7 shrink-0">
                    <AvatarFallback
                      className={
                        msg.role === "user"
                          ? "bg-primary text-primary-foreground text-xs"
                          : "bg-muted text-muted-foreground text-xs"
                      }
                    >
                      {msg.role === "user" ? (
                        <UserIcon className="size-3.5" />
                      ) : (
                        <BotIcon className="size-3.5" />
                      )}
                    </AvatarFallback>
                  </Avatar>

                  <div
                    className={`max-w-[80%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
                      msg.role === "user"
                        ? "bg-primary text-primary-foreground"
                        : "bg-muted text-foreground"
                    }`}
                  >
                    {msg.role === "assistant" ? (
                      <RichMessage
                        content={msg.content}
                        streaming={msg.streaming}
                        onAction={(text) => sendMessage(text)}
                      />
                    ) : (
                      <MessageContent content={msg.content} />
                    )}

                    {msg.agents_involved && msg.agents_involved.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {msg.agents_involved.map((agent) => (
                          <Badge
                            key={agent}
                            variant="outline"
                            className="text-[10px] font-normal"
                          >
                            {agent}
                          </Badge>
                        ))}
                      </div>
                    )}

                    {msg.role === "assistant" &&
                      msg.steps &&
                      msg.steps.length > 0 && <AgentTimeline steps={msg.steps} />}

                    {msg.role === "assistant" && msg.mode && (
                      <OrchestrationGraph
                        mode={msg.mode}
                        activeNodeIds={msg.activeNodeIds}
                        doneNodeIds={msg.doneNodeIds}
                        errorNodeIds={msg.errorNodeIds}
                      />
                    )}

                    {msg.role === "assistant" && !msg.streaming && msg.content && (
                      <MessageActions
                        text={msg.content}
                        onRetry={() => {
                          // Find the user message immediately preceding this assistant message
                          const msgIndex = messages.findIndex((m) => m.id === msg.id);
                          const userMsg = messages
                            .slice(0, msgIndex)
                            .findLast((m) => m.role === "user");
                          if (userMsg) sendMessage(userMsg.content);
                        }}
                      />
                    )}
                  </div>
                </div>
              ))}

              <AnimatePresence>
                {isResponding && messages[messages.length - 1]?.role !== "assistant" && (
                  <motion.div
                    key="thinking"
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={{ duration: 0.15 }}
                  >
                    <ThinkingIndicator label={thinkingLabel} />
                  </motion.div>
                )}
              </AnimatePresence>

              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* ---- Input area ---- */}
        <div className="border-t bg-background px-4 py-3">
          <div className="mx-auto max-w-3xl space-y-2">
            <div className="flex items-center justify-end">
              <ModeSwitcher value={orchestrationMode} onChange={handleModeChange} disabled={isResponding} />
            </div>
            <PromptInputBox
              onSend={(message, agentMode) => sendMessage(message, agentMode)}
              isLoading={isResponding}
              suggestions={DEMO_SCENARIOS.slice(0, 4).map((s) => ({
                label: s.label,
                prompt: s.prompt,
              }))}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
