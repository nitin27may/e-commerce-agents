import { describe, expect, it } from "vitest";
import { AGENTS } from "@/lib/agents";
import { specialistSummary, toolCount } from "./agent-copy";

describe("agent copy", () => {
  it("pluralises tool counts", () => {
    expect(toolCount(1)).toBe("1 tool");
    expect(toolCount(8)).toBe("8 tools");
  });

  it("counts the specialists it describes", () => {
    const specialists = AGENTS.filter((a) => a.slug !== "orchestrator").length;
    expect(specialistSummary(specialists)).toMatch(/^Five specialist agents handle/);
    expect(specialistSummary(1)).toMatch(/^One specialist agent handles/);
    expect(specialistSummary(12)).toMatch(/^12 specialist agents/);
  });
});
