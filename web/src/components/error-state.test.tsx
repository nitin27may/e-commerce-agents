import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ErrorState } from "./error-state";
import RootError from "@/app/error";
import AppError from "@/app/(app)/error";
import GlobalError from "@/app/global-error";
import NotFound from "@/app/not-found";

vi.mock("next/font/google", () => ({
  Inter: () => ({ variable: "font-inter" }),
}));

const SECRET = "ECONNREFUSED postgres://admin:hunter2@db:5432";

function boom(digest = "abc123") {
  return Object.assign(new Error(SECRET), { digest });
}

describe("ErrorState", () => {
  it("offers retry and a way home, and calls the retry handler", async () => {
    const retry = vi.fn();
    render(<ErrorState onRetry={retry} />);
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: /back to home/i })).toHaveAttribute("href", "/");
  });

  it("omits retry for not-found", () => {
    render(<ErrorState kind="not-found" />);
    expect(screen.queryByRole("button", { name: /try again/i })).toBeNull();
    expect(screen.getByRole("heading", { name: /page not found/i })).toBeInTheDocument();
  });
});

describe("error boundaries", () => {
  it("never render the raw error message", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { container } = render(<RootError error={boom()} unstable_retry={() => {}} />);
    expect(container.textContent).not.toContain("hunter2");
    expect(container.textContent).not.toContain("ECONNREFUSED");
    expect(screen.getByText(/Reference: abc123/)).toBeInTheDocument();
    spy.mockRestore();
  });

  it("(app) boundary points home to the dashboard and retries", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const retry = vi.fn();
    const { container } = render(<AppError error={boom()} unstable_retry={retry} />);
    expect(container.textContent).not.toContain("hunter2");
    expect(screen.getByRole("link", { name: /back to dashboard/i })).toHaveAttribute("href", "/home");
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(retry).toHaveBeenCalled();
    spy.mockRestore();
  });

  it("global boundary renders retry and does not leak the message", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    // <html>/<body> cannot nest inside the test container's <div>; React warns
    // but still renders the children, which is all this test needs.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    render(<GlobalError error={boom()} unstable_retry={() => {}} />);
    expect(document.body.textContent).not.toContain("hunter2");
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    spy.mockRestore();
    warn.mockRestore();
  });

  it("not-found page links home", () => {
    render(<NotFound />);
    expect(screen.getByRole("heading", { name: /page not found/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /back to home/i })).toHaveAttribute("href", "/");
  });
});
