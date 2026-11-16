"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Bell } from "lucide-react";
import { api, type AppNotification } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** How often the bell looks for new notifications. Modest on purpose: no websockets. */
export const POLL_INTERVAL_MS = 30_000;

/**
 * The sidebar bell: an unread badge and a short list of the caller's notifications
 * (their own plus any addressed to their role). Polls while mounted; a failed poll
 * leaves what is already shown. Selecting a notification marks it read.
 */
export function NotificationBell() {
  const [items, setItems] = useState<AppNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.getNotifications();
      setItems(res.notifications);
      setUnread(res.unread_count);
    } catch {
      // Keep the last good list; the next poll tries again.
    }
  }, []);

  useEffect(() => {
    const first = setTimeout(() => void load(), 0);
    const timer = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [load]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  async function markRead(n: AppNotification) {
    if (n.read) return;
    setItems((prev) => prev.map((x) => (x.id === n.id ? { ...x, read: true } : x)));
    setUnread((c) => Math.max(0, c - 1));
    try {
      await api.markNotificationRead(n.id);
    } catch {
      void load(); // put the real state back
    }
  }

  return (
    <div ref={rootRef} className="relative">
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setOpen((o) => !o)}
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
        aria-expanded={open}
        aria-haspopup="true"
      >
        <Bell className="size-4" />
        {unread > 0 && (
          <span
            data-testid="notification-badge"
            className="absolute -right-0.5 -top-0.5 flex min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-bold leading-4 text-primary-foreground"
          >
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </Button>

      {open && (
        <div
          role="region"
          aria-label="Notifications"
          className="absolute left-0 top-full z-50 mt-2 w-72 rounded-lg border bg-popover text-popover-foreground shadow-lg"
        >
          {items.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">No notifications yet.</p>
          ) : (
            <ul className="max-h-80 divide-y overflow-y-auto">
              {items.map((n) => {
                const content = (
                  <>
                    <span className="flex items-center gap-2 text-sm font-medium">
                      {!n.read && (
                        <span aria-label="Unread" className="size-2 shrink-0 rounded-full bg-primary" />
                      )}
                      {n.title}
                    </span>
                    {n.body && (
                      <span className="mt-0.5 block text-xs text-muted-foreground">{n.body}</span>
                    )}
                  </>
                );
                const cls = cn(
                  "block w-full px-3 py-2 text-left transition-colors hover:bg-accent",
                  n.read && "opacity-70",
                );
                return (
                  <li key={n.id}>
                    {n.link ? (
                      <Link
                        href={n.link}
                        className={cls}
                        onClick={() => {
                          void markRead(n);
                          setOpen(false);
                        }}
                      >
                        {content}
                      </Link>
                    ) : (
                      <button type="button" className={cls} onClick={() => void markRead(n)}>
                        {content}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
