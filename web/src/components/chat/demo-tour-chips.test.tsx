import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DemoResetButton, DemoTourChips, ReplayMissCallout } from "./demo-tour-chips";
import { api, type DemoTour } from "@/lib/api";
import { resetDemoTourCache } from "@/lib/demo-tour";

const useAuthMock = vi.fn();
vi.mock("@/lib/auth-context", () => ({ useAuth: () => useAuthMock() }));

const TOUR: DemoTour = {
  miss_message: "That one was not recorded.",
  tour_user: "tour@example.com",
  chips: [
    { id: "a", label: "Find headphones", prompt: "Show me headphones", mode: "tool", new_conversation: true },
    { id: "b", label: "Best reviews?", prompt: "Which has the best reviews?", mode: "tool", new_conversation: false },
    { id: "c", label: "Handoff", prompt: "Track my order", mode: "handoff", new_conversation: true },
  ],
};

beforeEach(() => {
  resetDemoTourCache();
  useAuthMock.mockReturnValue({ user: null });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DemoTourChips", () => {
  it("renders one chip per manifest entry and sends the chip with its mode", async () => {
    vi.spyOn(api, "getDemoTour").mockResolvedValue(TOUR);
    const onPick = vi.fn();
    render(<DemoTourChips onPick={onPick} />);

    await userEvent.click(await screen.findByRole("button", { name: "Handoff" }));

    expect(screen.getAllByRole("button")).toHaveLength(3);
    expect(onPick).toHaveBeenCalledWith(TOUR.chips[2]);
  });

  it("renders nothing when the backend is not a demo (404)", async () => {
    // The real client, the real 404: not a mock of getDemoTour.
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 404 }));
    const { container } = render(<DemoTourChips onPick={() => {}} />);

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the fetch itself fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));
    const { container } = render(<DemoTourChips onPick={() => {}} />);

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("disables the chips while a turn is running", async () => {
    vi.spyOn(api, "getDemoTour").mockResolvedValue(TOUR);
    render(<DemoTourChips onPick={() => {}} disabled />);

    expect(await screen.findByRole("button", { name: "Handoff" })).toBeDisabled();
  });
});

describe("ReplayMissCallout", () => {
  it("shows the server's message and re-offers the chips", async () => {
    vi.spyOn(api, "getDemoTour").mockResolvedValue(TOUR);
    const onPick = vi.fn();
    render(<ReplayMissCallout message="Not recorded, sorry." onPick={onPick} />);

    expect(screen.getByRole("status")).toHaveTextContent("Not recorded, sorry.");
    await userEvent.click(await screen.findByRole("button", { name: "Find headphones" }));
    expect(onPick).toHaveBeenCalledWith(TOUR.chips[0]);
  });

  it("falls back to the manifest's message when the frame carried none", async () => {
    vi.spyOn(api, "getDemoTour").mockResolvedValue(TOUR);
    render(<ReplayMissCallout message="" onPick={() => {}} />);

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("That one was not recorded."));
  });
});

describe("DemoResetButton", () => {
  it("is shown to the tour user only", async () => {
    vi.spyOn(api, "getDemoTour").mockResolvedValue(TOUR);
    useAuthMock.mockReturnValue({ user: { email: "alice.johnson@gmail.com" } });
    const { container } = render(<DemoResetButton onReset={() => {}} />);

    await waitFor(() => expect(api.getDemoTour).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("resets through the API and tells the page", async () => {
    vi.spyOn(api, "getDemoTour").mockResolvedValue(TOUR);
    const reset = vi.spyOn(api, "resetDemo").mockResolvedValue({ status: "reset" });
    useAuthMock.mockReturnValue({ user: { email: "tour@example.com" } });
    const onReset = vi.fn();
    render(<DemoResetButton onReset={onReset} />);

    await userEvent.click(await screen.findByRole("button", { name: /reset demo/i }));

    await waitFor(() => expect(onReset).toHaveBeenCalled());
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("says so, and does not clear the thread, when the reset fails", async () => {
    vi.spyOn(api, "getDemoTour").mockResolvedValue(TOUR);
    vi.spyOn(api, "resetDemo").mockRejectedValue(new Error("boom"));
    useAuthMock.mockReturnValue({ user: { email: "tour@example.com" } });
    const onReset = vi.fn();
    render(<DemoResetButton onReset={onReset} />);

    await userEvent.click(await screen.findByRole("button", { name: /reset demo/i }));

    expect(await screen.findByText("Reset failed")).toBeInTheDocument();
    expect(onReset).not.toHaveBeenCalled();
  });
});
