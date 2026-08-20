import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Activity } from "lucide-react";
import { StatCard } from "./stat-card";

describe("StatCard", () => {
  it("renders label and value", () => {
    render(<StatCard label="Invocations" value="123.4K" />);
    expect(screen.getByText("Invocations")).toBeInTheDocument();
    expect(screen.getByText("123.4K")).toBeInTheDocument();
  });

  it("renders a delta with the trend styling", () => {
    render(
      <StatCard
        label="Defects"
        value={11}
        delta={{ value: "+3 this week", trend: "up" }}
      />,
    );
    const delta = screen.getByText("+3 this week");
    expect(delta).toBeInTheDocument();
    expect(delta.className).toContain("text-success");
  });

  it("renders an optional hint and decorative icon", () => {
    const { container } = render(
      <StatCard label="Tokens" value="4.2M" icon={Activity} hint="last 24h" />,
    );
    expect(screen.getByText("last 24h")).toBeInTheDocument();
    // icon is decorative
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden");
  });
});
