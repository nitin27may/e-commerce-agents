import { describe, expect, it } from "vitest";
import {
  DEMO_SCENARIOS,
  QUICK_PROMPTS,
  chatPromptHref,
  shopAssistantHref,
} from "./scenarios";

describe("DEMO_SCENARIOS", () => {
  it("has exactly 8 scenarios", () => {
    expect(DEMO_SCENARIOS).toHaveLength(8);
  });

  it("every scenario has a non-empty id, label, description and prompt, and names no agent", () => {
    for (const s of DEMO_SCENARIOS) {
      expect(s.id, `${s.label} missing id`).toBeTruthy();
      expect(s.label, `${s.id} missing label`).toBeTruthy();
      expect(s.chipLabel, `${s.id} missing chipLabel`).toBeTruthy();
      // A chip is what the shopper would say, so it never reads as a feature name.
      expect(s.chipLabel, `${s.id} chip repeats the feature name`).not.toBe(s.label);
      expect(s.description, `${s.id} missing description`).toBeTruthy();
      expect(s.prompt, `${s.id} missing prompt`).toBeTruthy();
      // Customer-facing: a scenario says what the shopper gets, never which agent does it.
      expect(s, `${s.id} names agents`).not.toHaveProperty("agents");
      expect(s.icon, `${s.id} missing icon`).toBeTruthy();
    }
  });

  it("ids are unique", () => {
    const ids = DEMO_SCENARIOS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("QUICK_PROMPTS", () => {
  it("exposes the first 4 scenarios as chip-label/prompt pairs", () => {
    expect(QUICK_PROMPTS).toHaveLength(4);
    for (let i = 0; i < 4; i++) {
      expect(QUICK_PROMPTS[i].label).toBe(DEMO_SCENARIOS[i].chipLabel);
      expect(QUICK_PROMPTS[i].prompt).toBe(DEMO_SCENARIOS[i].prompt);
    }
  });
});

describe("chatPromptHref", () => {
  it("builds an encoded chat deep-link", () => {
    expect(chatPromptHref("find a gift & deal")).toBe(
      "/chat?prompt=find%20a%20gift%20%26%20deal",
    );
  });
});

describe("shopAssistantHref", () => {
  it("builds an encoded public assistant deep-link", () => {
    expect(shopAssistantHref("check stock")).toBe(
      "/shop/assistant?prompt=check%20stock",
    );
  });
});
