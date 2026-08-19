import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ModeComparison } from "./mode-comparison";
import { api, type CompareResponse, type OrchestrationMode } from "@/lib/api";

const MODES: OrchestrationMode[] = [
  {
    name: "tool",
    label: "Tool Router",
    description: "d",
    capabilities: { streams: true, supports_hitl: true, supports_checkpoints: false, is_graph: false },
    default: true,
  },
  {
    name: "workflow:pre-purchase",
    label: "Pre-Purchase Research",
    description: "d",
    capabilities: { streams: true, supports_hitl: false, supports_checkpoints: false, is_graph: true },
    default: false,
  },
];

vi.mock("mermaid", () => ({
  default: { initialize: vi.fn(), render: vi.fn(async () => ({ svg: "<svg></svg>" })) },
}));

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ModeComparison", () => {
  it("fetches modes and renders them as toggleable chips once the dialog opens", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    render(<ModeComparison />);

    fireEvent.click(screen.getByRole("button", { name: /Compare/ }));

    await waitFor(() => expect(screen.getByText("Pre-Purchase Research")).toBeInTheDocument());
  });

  it("disables Run comparison until a prompt is entered and 2+ modes are selected", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    render(<ModeComparison />);
    fireEvent.click(screen.getByRole("button", { name: /Compare/ }));
    await waitFor(() => expect(screen.getByText("Pre-Purchase Research")).toBeInTheDocument());

    // Only "tool" is selected by default (the one mode marked default: true).
    const runButton = screen.getByRole("button", { name: /Run comparison/ });
    expect(runButton).toBeDisabled();

    fireEvent.click(screen.getByText("Pre-Purchase Research"));
    expect(runButton).toBeDisabled(); // still no prompt text

    fireEvent.change(screen.getByPlaceholderText(/headphones/), { target: { value: "worth it?" } });
    expect(runButton).not.toBeDisabled();
  });

  it("runs the comparison and renders a result card per mode", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    const compareResponse: CompareResponse = {
      message: "worth it?",
      results: [
        {
          mode: "tool",
          label: "Tool Router",
          text: "Yes, it's a solid buy.",
          latency_ms: 420,
          agents_involved: ["orchestrator"],
          step_count: 1,
          graph_mermaid: null,
          error: null,
        },
        {
          mode: "workflow:pre-purchase",
          label: "Pre-Purchase Research",
          text: "Stock: 10 units | Price trend: stable",
          latency_ms: 180,
          agents_involved: ["reviews", "stock"],
          step_count: 6,
          graph_mermaid: "graph LR\n  fan_out[fan-out] --> reviews\n  fan_out --> stock\n",
          error: null,
        },
      ],
    };
    vi.spyOn(api, "compareModes").mockResolvedValue(compareResponse);

    render(<ModeComparison />);
    fireEvent.click(screen.getByRole("button", { name: /Compare/ }));
    await waitFor(() => expect(screen.getByText("Pre-Purchase Research")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Pre-Purchase Research"));
    fireEvent.change(screen.getByPlaceholderText(/headphones/), { target: { value: "worth it?" } });
    fireEvent.click(screen.getByRole("button", { name: /Run comparison/ }));

    await waitFor(() => expect(screen.getByText("Yes, it's a solid buy.")).toBeInTheDocument());
    expect(screen.getByText("Stock: 10 units | Price trend: stable")).toBeInTheDocument();
    expect(screen.getByText("420ms")).toBeInTheDocument();
    expect(screen.getByText("180ms")).toBeInTheDocument();
    expect(api.compareModes).toHaveBeenCalledWith("worth it?", ["tool", "workflow:pre-purchase"]);
  });

  it("shows a mode's error inline without hiding the others", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    vi.spyOn(api, "compareModes").mockResolvedValue({
      message: "x",
      results: [
        {
          mode: "tool",
          label: "Tool Router",
          text: "ok",
          latency_ms: 10,
          agents_involved: [],
          step_count: 0,
          graph_mermaid: null,
          error: null,
        },
        {
          mode: "workflow:pre-purchase",
          label: "Pre-Purchase Research",
          text: "",
          latency_ms: 0,
          agents_involved: [],
          step_count: 0,
          graph_mermaid: null,
          error: "Couldn't find a product matching 'x'.",
        },
      ],
    });

    render(<ModeComparison />);
    fireEvent.click(screen.getByRole("button", { name: /Compare/ }));
    await waitFor(() => expect(screen.getByText("Pre-Purchase Research")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Pre-Purchase Research"));
    fireEvent.change(screen.getByPlaceholderText(/headphones/), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: /Run comparison/ }));

    await waitFor(() => expect(screen.getByText("ok")).toBeInTheDocument());
    expect(screen.getByText("Couldn't find a product matching 'x'.")).toBeInTheDocument();
  });
});
