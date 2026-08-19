import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AgentTimeline } from "./agent-timeline";
import type { AgentStep } from "@/lib/api";

const STEPS: AgentStep[] = [
  {
    agent: "orchestrator",
    tool_name: "call_specialist_agent",
    tool_input: { agent_name: "product-discovery" },
    tool_output: { response: "Found 3 items" },
    status: "success",
    duration_ms: 120,
  },
  {
    agent: "product-discovery",
    tool_name: "search_products",
    tool_input: { query: "headphones", max_price: 300 },
    tool_output: { count: 3 },
    status: "success",
    duration_ms: 89,
  },
  {
    agent: "product-discovery",
    tool_name: "check_stock",
    tool_input: { product_id: "abc" },
    tool_output: null,
    status: "error",
    duration_ms: 12,
  },
];

describe("AgentTimeline", () => {
  it("renders nothing when steps array is empty", () => {
    const { container } = render(<AgentTimeline steps={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows step rows expanded by default — the timeline is the point, not a click away from it", () => {
    render(<AgentTimeline steps={STEPS} />);
    expect(screen.getByText(/Agent activity · 3 steps/)).toBeInTheDocument();
    expect(screen.getByText("search_products")).toBeInTheDocument();
    expect(screen.getByText("call_specialist_agent")).toBeInTheDocument();
    expect(screen.getByText("check_stock")).toBeInTheDocument();
  });

  it("shows total duration in the header", () => {
    render(<AgentTimeline steps={STEPS} />);
    // 120 + 89 + 12 = 221ms total
    expect(screen.getByText("221ms total")).toBeInTheDocument();
  });

  it("collapses to hide step rows on click", () => {
    render(<AgentTimeline steps={STEPS} />);
    fireEvent.click(screen.getByRole("button", { name: /Agent activity/ }));
    expect(screen.queryByText("search_products")).not.toBeInTheDocument();
  });

  it("shows agent label on each row", () => {
    render(<AgentTimeline steps={STEPS} />);
    expect(screen.getAllByText("orchestrator").length).toBeGreaterThan(0);
    expect(screen.getAllByText("product-discovery").length).toBeGreaterThan(0);
  });

  it("shows duration per step", () => {
    render(<AgentTimeline steps={STEPS} />);
    expect(screen.getByText("120ms")).toBeInTheDocument();
    expect(screen.getByText("89ms")).toBeInTheDocument();
  });

  it("expands a step row to reveal tool_input and tool_output", () => {
    render(<AgentTimeline steps={STEPS} />);
    // Click the search_products step row to expand it
    fireEvent.click(screen.getByText("search_products").closest("button")!);
    expect(screen.getByText("Input")).toBeInTheDocument();
    expect(screen.getByText("Output")).toBeInTheDocument();
    // tool_input JSON should be visible
    expect(screen.getByText(/headphones/)).toBeInTheDocument();
  });

  it("does not show expand chevron for steps without tool_input or tool_output", () => {
    const noDetailStep: AgentStep[] = [
      { agent: "orchestrator", tool_name: "noop", status: "success", duration_ms: 1 },
    ];
    render(<AgentTimeline steps={noDetailStep} />);
    // The step button should be disabled (no detail to expand)
    const stepBtn = screen.getByText("noop").closest("button")!;
    expect(stepBtn).toBeDisabled();
  });

  it("uses 'orchestrator' fallback when agent field is missing", () => {
    const steps: AgentStep[] = [
      { tool_name: "some_tool", status: "success", duration_ms: 5 },
    ];
    render(<AgentTimeline steps={steps} />);
    expect(screen.getByText("orchestrator")).toBeInTheDocument();
  });
});
