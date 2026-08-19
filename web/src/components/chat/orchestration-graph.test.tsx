import { describe, expect, it, vi, afterEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { OrchestrationGraph } from "./orchestration-graph";
import { api } from "@/lib/api";

const SAMPLE_GRAPH =
  "graph LR\n" +
  "  fan_out[fan-out] --> reviews\n" +
  "  fan_out --> stock\n" +
  "  reviews --> merge_and_ship[merge-and-ship]\n" +
  "  stock --> merge_and_ship\n";

vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async (_id: string, source: string) => ({ svg: `<svg data-source="${encodeURIComponent(source)}"></svg>` })),
  },
}));

afterEach(() => {
  vi.restoreAllMocks();
});

describe("OrchestrationGraph", () => {
  it("renders nothing for a mode with no fixed graph (mermaid: null)", async () => {
    vi.spyOn(api, "getModeGraph").mockResolvedValue({ name: "tool", mermaid: null });
    const { container } = render(<OrchestrationGraph mode="tool" />);
    await waitFor(() => expect(api.getModeGraph).toHaveBeenCalledWith("tool"));
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing while the graph fetch is still pending or fails", async () => {
    vi.spyOn(api, "getModeGraph").mockRejectedValue(new Error("network error"));
    const { container } = render(<OrchestrationGraph mode="workflow:pre-purchase" />);
    await waitFor(() => expect(api.getModeGraph).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it("renders a container once the graph loads", async () => {
    vi.spyOn(api, "getModeGraph").mockResolvedValue({ name: "workflow:pre-purchase", mermaid: SAMPLE_GRAPH });
    const { container } = render(<OrchestrationGraph mode="workflow:pre-purchase" />);
    await waitFor(() => expect(container.querySelector("svg")).not.toBeNull());
  });

  it("classes an active node id (dash form) as the mermaid active class", async () => {
    vi.spyOn(api, "getModeGraph").mockResolvedValue({ name: "workflow:pre-purchase", mermaid: SAMPLE_GRAPH });
    const { container } = render(<OrchestrationGraph mode="workflow:pre-purchase" activeNodeIds={["fan-out"]} />);
    await waitFor(() => expect(container.querySelector("svg")).not.toBeNull());
    const encoded = container.querySelector("svg")!.getAttribute("data-source")!;
    const source = decodeURIComponent(encoded);
    expect(source).toContain("class fan_out active");
  });

  it("classes a done node id as success and defaults untouched nodes to core", async () => {
    vi.spyOn(api, "getModeGraph").mockResolvedValue({ name: "workflow:pre-purchase", mermaid: SAMPLE_GRAPH });
    const { container } = render(<OrchestrationGraph mode="workflow:pre-purchase" doneNodeIds={["fan-out"]} />);
    await waitFor(() => expect(container.querySelector("svg")).not.toBeNull());
    const source = decodeURIComponent(container.querySelector("svg")!.getAttribute("data-source")!);
    expect(source).toContain("class fan_out success");
    expect(source).toContain("class reviews core");
  });

  it("re-fetches the graph when the mode prop changes", async () => {
    vi.spyOn(api, "getModeGraph").mockResolvedValue({ name: "workflow:pre-purchase", mermaid: SAMPLE_GRAPH });
    const { rerender } = render(<OrchestrationGraph mode="workflow:pre-purchase" />);
    await waitFor(() => expect(api.getModeGraph).toHaveBeenCalledWith("workflow:pre-purchase"));

    rerender(<OrchestrationGraph mode="workflow:return-replace" />);
    await waitFor(() => expect(api.getModeGraph).toHaveBeenCalledWith("workflow:return-replace"));
  });
});
