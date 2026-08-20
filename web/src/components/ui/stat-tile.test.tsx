import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatTile } from "./stat-tile";

describe("StatTile", () => {
  it("renders label and value", () => {
    render(<StatTile label="In Stock" value={42} />);
    expect(screen.getByText("In Stock")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("colors the value via tone", () => {
    render(<StatTile label="Risk Level" value="High" tone="destructive" />);
    expect(screen.getByText("High").className).toContain("text-destructive");
  });

  it("has no tone color by default", () => {
    render(<StatTile label="Reviews" value={128} />);
    expect(screen.getByText("128").className).not.toContain("text-success");
    expect(screen.getByText("128").className).not.toContain("text-destructive");
  });

  it("renders an optional hint", () => {
    render(<StatTile label="Sentiment" value="Positive" tone="success" hint="last 30 days" />);
    expect(screen.getByText("last 30 days")).toBeInTheDocument();
  });
});
