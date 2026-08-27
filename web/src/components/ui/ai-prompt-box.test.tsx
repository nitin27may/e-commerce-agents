import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AGENT_MODES, PromptInputBox } from "./ai-prompt-box";

/**
 * The composer had no test at all before issue #4, which is how it acquired a
 * permanently-visible six-chip mode row nobody had to justify. These cover the
 * two things #4 changed — the collapsed specialist picker and the suggestion
 * row — plus the send path they both feed, since a picker that looks right and
 * sends the wrong mode is the failure that matters.
 */

const SUGGESTIONS = [
  { label: "Check stock", prompt: "Is this in stock?" },
  { label: "Show reviews", prompt: "What do the reviews say?" },
  { label: "Find similar", prompt: "Show me similar products" },
  { label: "Fourth", prompt: "never rendered" },
];

const openPicker = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByRole("button", { name: "Specialist" }));
  return screen.getByRole("listbox", { name: "Specialist" });
};

/**
 * Both the picker and the suggestion row leave via an `AnimatePresence` exit
 * transition, so they stay mounted — at a fading opacity — for a frame or two
 * after the state that renders them flips. Asserting synchronously races that
 * exit and fails on a component that is behaving correctly.
 */
const expectGone = (role: string, name?: string) =>
  waitFor(() =>
    expect(screen.queryByRole(role, name ? { name } : undefined)).not.toBeInTheDocument()
  );

describe("PromptInputBox — specialist picker", () => {
  it("shows one collapsed control, not a chip per mode", () => {
    // The regression this file exists for: six always-visible chips took the
    // top third of the composer to expose a control most turns never touch.
    render(<PromptInputBox onSend={() => {}} />);

    expect(screen.getByRole("button", { name: "Specialist" })).toHaveTextContent("Auto");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    for (const mode of AGENT_MODES.slice(1)) {
      expect(screen.queryByRole("button", { name: mode.label })).not.toBeInTheDocument();
    }
  });

  it("lists every mode once opened, with the active one marked selected", async () => {
    const user = userEvent.setup();
    render(<PromptInputBox onSend={() => {}} />);

    const listbox = await openPicker(user);
    const options = within(listbox).getAllByRole("option");

    expect(options.map((o) => o.textContent)).toEqual(AGENT_MODES.map((m) => m.label));
    expect(options[0]).toHaveAttribute("aria-selected", "true");
  });

  it("collapses back to the chosen mode after a selection", async () => {
    const user = userEvent.setup();
    render(<PromptInputBox onSend={() => {}} />);

    const listbox = await openPicker(user);
    await user.click(within(listbox).getByRole("option", { name: "Orders" }));

    expect(screen.getByRole("button", { name: "Specialist" })).toHaveTextContent("Orders");
    await expectGone("listbox");
  });

  it("swaps the placeholder to match the pinned specialist", async () => {
    // The placeholder is the only on-screen hint about what a pinned mode
    // expects, now that the labels are behind a menu.
    const user = userEvent.setup();
    render(<PromptInputBox onSend={() => {}} />);

    const listbox = await openPicker(user);
    await user.click(within(listbox).getByRole("option", { name: "Pricing" }));

    expect(screen.getByPlaceholderText(/deals, coupons, or price trends/i)).toBeInTheDocument();
  });

  it("closes on Escape and on an outside click", async () => {
    // A popover that only closes via its own trigger traps whoever opened it
    // by accident — the most likely way this one gets opened.
    const user = userEvent.setup();
    render(<PromptInputBox onSend={() => {}} />);

    await openPicker(user);
    await user.keyboard("{Escape}");
    await expectGone("listbox");

    await openPicker(user);
    await user.click(document.body);
    await expectGone("listbox");
  });

  it("sends the pinned mode id, not its label", async () => {
    // AGENT_MODES ids are backend agent names; sending "Orders" instead of
    // "order-management" fails server-side, well away from this component.
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<PromptInputBox onSend={onSend} />);

    const listbox = await openPicker(user);
    await user.click(within(listbox).getByRole("option", { name: "Orders" }));
    await user.type(screen.getByRole("textbox"), "where is my order{Enter}");

    expect(onSend).toHaveBeenCalledWith("where is my order", "order-management");
  });

  it("sends null for Auto so the orchestrator routes", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<PromptInputBox onSend={onSend} />);

    await user.type(screen.getByRole("textbox"), "hello{Enter}");

    expect(onSend).toHaveBeenCalledWith("hello", null);
  });
});

describe("PromptInputBox — suggestions", () => {
  it("renders at most three, in order", () => {
    // The row is one line by design; a fourth chip wraps and shifts the
    // composer's height between turns.
    render(<PromptInputBox onSend={() => {}} suggestions={SUGGESTIONS} />);

    expect(screen.getByRole("button", { name: "Check stock" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Find similar" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Fourth" })).not.toBeInTheDocument();
  });

  it("fills the composer with the prompt rather than sending it", async () => {
    // Sending straight from a chip removes the chance to edit, and one
    // mis-click then costs a live LLM turn.
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<PromptInputBox onSend={onSend} suggestions={SUGGESTIONS} />);

    await user.click(screen.getByRole("button", { name: "Show reviews" }));

    expect(screen.getByRole("textbox")).toHaveValue("What do the reviews say?");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("hides the row once there is input, and while a response streams", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<PromptInputBox onSend={() => {}} suggestions={SUGGESTIONS} />);

    await user.type(screen.getByRole("textbox"), "a");
    await expectGone("button", "Check stock");

    rerender(<PromptInputBox onSend={() => {}} suggestions={SUGGESTIONS} isLoading />);
    await expectGone("button", "Check stock");
  });

  it("renders no row at all when there is nothing to suggest", () => {
    render(<PromptInputBox onSend={() => {}} suggestions={[]} />);
    expect(screen.queryByRole("button", { name: "Check stock" })).not.toBeInTheDocument();
  });
});
