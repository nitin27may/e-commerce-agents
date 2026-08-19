import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ModeSwitcher } from "./mode-switcher";
import { api, type OrchestrationMode } from "@/lib/api";

const MODES: OrchestrationMode[] = [
  {
    name: "tool",
    label: "Tool Router",
    description: "The orchestrator LLM calls call_specialist_agent to route to a specialist.",
    capabilities: { streams: true, supports_hitl: true, supports_checkpoints: false, is_graph: false },
    default: true,
  },
  {
    name: "workflow:return-replace",
    label: "Return & Replace (sequential + in-workflow HITL)",
    description: "MAF sequential workflow with an in-workflow HITL gate.",
    capabilities: { streams: true, supports_hitl: true, supports_checkpoints: true, is_graph: true },
    default: false,
  },
];

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ModeSwitcher", () => {
  it("renders nothing before modes load and stays empty if the fetch fails", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockRejectedValue(new Error("network error"));
    const { container } = render(<ModeSwitcher value="" onChange={() => {}} />);
    await waitFor(() => expect(container.firstChild).toBeNull());
  });

  it("shows the selected mode's label once modes load", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    render(<ModeSwitcher value="tool" onChange={() => {}} />);
    await waitFor(() => expect(screen.getByText("Tool Router")).toBeInTheDocument());
  });

  it("shows a 'Mode' placeholder, not a blank trigger, when no mode is selected yet", async () => {
    // Regression test: base-ui's SelectValue `placeholder` prop is ignored
    // once `children` is a render function — found live in a browser, where
    // an empty `value` rendered a blank trigger instead of "Mode".
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    render(<ModeSwitcher value="" onChange={() => {}} />);
    await waitFor(() => expect(screen.getByText("Mode")).toBeInTheDocument());
  });

  it("shows capability chips for a mode with HITL + checkpoints + graph", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    render(<ModeSwitcher value="workflow:return-replace" onChange={() => {}} />);
    await waitFor(() => expect(screen.getByText("graph")).toBeInTheDocument());
    expect(screen.getByText("HITL")).toBeInTheDocument();
    expect(screen.getByText("checkpoints")).toBeInTheDocument();
  });

  it("shows no capability chips for the plain tool mode", async () => {
    vi.spyOn(api, "getOrchestrationModes").mockResolvedValue(MODES);
    render(<ModeSwitcher value="tool" onChange={() => {}} />);
    await waitFor(() => expect(screen.getByText("Tool Router")).toBeInTheDocument());
    expect(screen.queryByText("graph")).not.toBeInTheDocument();
    expect(screen.queryByText("checkpoints")).not.toBeInTheDocument();
  });
});
