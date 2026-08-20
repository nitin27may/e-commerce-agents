import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { CheckCircle } from "lucide-react";
import { StatusBadge } from "./status-badge";

describe("StatusBadge", () => {
  it("renders the label with the tone's token classes", () => {
    render(<StatusBadge label="In Stock" tone="success" />);
    const badge = screen.getByText("In Stock");
    expect(badge.className).toContain("text-success");
  });

  it.each([
    ["success", "text-success"],
    ["warning", "text-warning"],
    ["info", "text-info"],
    ["destructive", "text-destructive"],
    ["neutral", "text-muted-foreground"],
  ] as const)("tone=%s maps to %s", (tone, expectedClass) => {
    render(<StatusBadge label="Status" tone={tone} />);
    expect(screen.getByText("Status").className).toContain(expectedClass);
  });

  it("renders an optional icon", () => {
    const { container } = render(<StatusBadge label="Delivered" tone="success" icon={CheckCircle} />);
    expect(container.querySelector("svg")).toBeInTheDocument();
  });
});
