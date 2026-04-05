"use client";

import {
  useState,
  useEffect,
  useRef,
  useCallback,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { RichMessage } from "@/components/chat/rich-message";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
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
import {
  MessageSquarePlusIcon,
  Trash2Icon,
  SendIcon,
  PanelLeftIcon,
  BotIcon,
  UserIcon,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  agents_involved?: string[];
  created_at?: string;
}

interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Typing indicator
// ---------------------------------------------------------------------------

function TypingDots() {
  return (
    <div className="flex items-center gap-2 px-1 py-3">
      <Avatar className="size-7 shrink-0">
        <AvatarFallback className="bg-teal-100 text-teal-700 text-xs">
          <BotIcon className="size-3.5" />
        </AvatarFallback>
      </Avatar>
      <div className="flex items-center gap-1">
        <span className="inline-block size-2 animate-bounce rounded-full bg-slate-400 [animation-delay:0ms]" />
        <span className="inline-block size-2 animate-bounce rounded-full bg-slate-400 [animation-delay:150ms]" />
        <span className="inline-block size-2 animate-bounce rounded-full bg-slate-400 [animation-delay:300ms]" />
      </div>
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

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isResponding, setIsResponding] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);

  const searchParams = useSearchParams();
  const pendingQueryRef = useRef<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // ---- Load conversations on mount ----
  useEffect(() => {
    if (!isAuthenticated) return;
    loadConversations();
  }, [isAuthenticated]);

  // ---- Auto-send ?q= query param on mount ----
  useEffect(() => {
    if (!isAuthenticated) return;
    const q = searchParams.get("q");
    if (q) {
      pendingQueryRef.current = q;
    }
  }, [isAuthenticated, searchParams]);

  // Fire the pending query once conversations have loaded (initial mount)
  useEffect(() => {
    if (pendingQueryRef.current && !isResponding) {
      const q = pendingQueryRef.current;
      pendingQueryRef.current = null;
      setInput(q);
      // Use a microtask so setInput has committed before we send
      queueMicrotask(() => {
        sendMessage(q);
      });
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
    loadMessages(activeConversationId);
  }, [activeConversationId]);

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
    setInput("");
    setSheetOpen(false);
    textareaRef.current?.focus();
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
  async function sendMessage(text: string) {
    if (!text.trim() || isResponding) return;

    const trimmed = text.trim();

    // Optimistic user message
    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: trimmed,
    };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsResponding(true);

    try {
      const response = await api.chat(trimmed, activeConversationId ?? undefined);

      // If this was the first message, a new conversation was created
      if (!activeConversationId && response.conversation_id) {
        setActiveConversationId(response.conversation_id);
        await loadConversations();
      }

      const assistantMessage: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: response.response,
        agents_involved: response.agents_involved,
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err) {
      const errMsg =
        err instanceof Error ? err.message : "Something went wrong.";
      const errorMessage: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: `Error: ${errMsg}`,
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsResponding(false);
      textareaRef.current?.focus();
    }
  }

  // ---- Send message (form handler) ----
  async function handleSend(e?: FormEvent) {
    e?.preventDefault();
    await sendMessage(input);
  }

  // ---- Keyboard: Enter sends, Shift+Enter adds newline ----
  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
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
                          ? "bg-teal-600 text-white text-xs"
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
                        ? "bg-teal-600 text-white"
                        : "bg-muted text-foreground"
                    }`}
                  >
                    {msg.role === "assistant" ? (
                      <RichMessage content={msg.content} onAction={(text) => sendMessage(text)} />
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
                  </div>
                </div>
              ))}

              {isResponding && <TypingDots />}

              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* ---- Input area ---- */}
        <div className="border-t bg-background px-4 py-3">
          <form
            onSubmit={handleSend}
            className="mx-auto flex max-w-3xl items-end gap-2"
          >
            <Textarea
              ref={textareaRef}
              placeholder="Type your message..."
              className="min-h-[40px] max-h-40 resize-none"
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isResponding}
            />
            <Button
              type="submit"
              size="icon"
              disabled={isResponding || !input.trim()}
              className="shrink-0 bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-40"
              title="Send message"
            >
              <SendIcon className="size-4" />
            </Button>
          </form>
          <p className="mx-auto mt-1.5 max-w-3xl text-center text-[11px] text-muted-foreground">
            Powered by AgentBazaar multi-agent orchestration
          </p>
        </div>
      </div>
    </div>
  );
}
