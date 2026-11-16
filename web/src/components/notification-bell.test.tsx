import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/lib/api", () => ({
  api: { getNotifications: vi.fn(), markNotificationRead: vi.fn() },
}));

import { api } from "@/lib/api";
import { NotificationBell, POLL_INTERVAL_MS } from "./notification-bell";

const note = (over: Partial<Record<string, unknown>> = {}) => ({
  id: "n1",
  kind: "approval.reminder",
  title: "An approval is waiting for review",
  body: "A return_approval review is due soon.",
  link: null,
  approval_id: null,
  created_at: "2026-11-16T10:00:00+00:00",
  read: false,
  ...over,
});

const getNotifications = vi.mocked(api.getNotifications);
const markRead = vi.mocked(api.markNotificationRead);

describe("NotificationBell", () => {
  beforeEach(() => {
    getNotifications.mockReset();
    markRead.mockReset().mockResolvedValue({ ok: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the unread count and lists notifications when opened", async () => {
    getNotifications.mockResolvedValue({ notifications: [note()], unread_count: 1 });
    const user = userEvent.setup();
    render(<NotificationBell />);

    expect(await screen.findByTestId("notification-badge")).toHaveTextContent("1");
    await user.click(screen.getByRole("button", { name: /notifications, 1 unread/i }));

    expect(screen.getByText("An approval is waiting for review")).toBeInTheDocument();
  });

  it("shows no badge and an empty state when there is nothing", async () => {
    getNotifications.mockResolvedValue({ notifications: [], unread_count: 0 });
    const user = userEvent.setup();
    render(<NotificationBell />);

    await waitFor(() => expect(getNotifications).toHaveBeenCalled());
    expect(screen.queryByTestId("notification-badge")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Notifications" }));
    expect(screen.getByText(/no notifications yet/i)).toBeInTheDocument();
  });

  it("marks a notification read and drops the badge", async () => {
    getNotifications.mockResolvedValue({ notifications: [note()], unread_count: 1 });
    const user = userEvent.setup();
    render(<NotificationBell />);
    await user.click(await screen.findByRole("button", { name: /1 unread/i }));

    await user.click(screen.getByText("An approval is waiting for review"));

    expect(markRead).toHaveBeenCalledWith("n1");
    expect(screen.queryByTestId("notification-badge")).toBeNull();
  });

  it("polls again after the interval, and survives a failed poll", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getNotifications.mockResolvedValueOnce({ notifications: [], unread_count: 0 });
    getNotifications.mockRejectedValueOnce(new Error("offline"));
    getNotifications.mockResolvedValue({ notifications: [note()], unread_count: 1 });
    render(<NotificationBell />);
    await waitFor(() => expect(getNotifications).toHaveBeenCalledTimes(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    expect(getNotifications).toHaveBeenCalledTimes(2); // the failure changed nothing
    expect(screen.queryByTestId("notification-badge")).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    expect(await screen.findByTestId("notification-badge")).toHaveTextContent("1");
  });
});
