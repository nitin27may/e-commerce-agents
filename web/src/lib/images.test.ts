import { describe, expect, it } from "vitest";
import { categoryImageUrl, productImageOverride, productImageUrl } from "./images";

const KINDLE_SEED =
  "https://images.unsplash.com/photo-1585790050230-5dd28404ccb9?w=400&h=400&fit=crop";

describe("productImageUrl", () => {
  it("uses the stored image, resized, when there is no override", () => {
    const url = productImageUrl(
      "p1",
      120,
      90,
      "https://images.unsplash.com/photo-1532012197267-da84d127e765?w=400&h=400&fit=crop",
      "Books",
      "Designing Data-Intensive Applications",
    );
    expect(url).toBe(
      "https://images.unsplash.com/photo-1532012197267-da84d127e765?w=120&h=90&fit=crop",
    );
  });

  it("swaps a mismatched seed photo for the override, matched by name", () => {
    const url = productImageUrl("p2", 480, 360, KINDLE_SEED, "Electronics", "Kindle Paperwhite Signature");
    expect(url).toContain("photo-1555631545-9dc21031669b");
    expect(url).toContain("w=480");
    expect(url).toContain("h=360");
    expect(url).not.toContain("1585790050230");
  });

  it("matches the override by seed photo when the caller has no name", () => {
    expect(productImageUrl("p2", 100, 100, KINDLE_SEED, "Electronics")).toContain(
      "photo-1555631545-9dc21031669b",
    );
  });

  it("matches names case-insensitively and ignores unknown names", () => {
    expect(productImageOverride("  dyson v15 detect ")).toContain("photo-1765970101634");
    expect(productImageOverride("Some other product")).toBeUndefined();
    expect(productImageOverride(undefined, undefined)).toBeUndefined();
  });

  it("falls back to the category image, then the generic image", () => {
    expect(productImageUrl("p3", 200, 200, null, "books")).toBe(categoryImageUrl("Books", 200, 200));
    expect(productImageUrl("p4", 200, 200)).toContain("w=200");
  });
});
