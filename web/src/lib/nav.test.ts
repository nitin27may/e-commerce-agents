import { describe, expect, it } from "vitest";
import { visibleGroups, labelForPath } from "./nav";

describe("visibleGroups", () => {
  it("shows shop + account to a plain customer, hides admin/seller", () => {
    const groups = visibleGroups({ isAdmin: false, isSeller: false });
    const labels = groups.flatMap((g) => g.items.map((i) => i.label));
    expect(labels).toContain("Home");
    expect(labels).toContain("Chat");
    expect(labels).toContain("Agents"); // visible to all authenticated users
    expect(labels).toContain("Runs");   // Phase 4 — visible to all users
    expect(labels).toContain("Profile");
    expect(labels).not.toContain("Usage"); // adminOnly
    expect(labels).not.toContain("Seller"); // sellerOnly
    expect(labels).not.toContain("Marketplace");
    expect(groups.find((g) => g.label === "Admin")).toBeUndefined();
  });

  it("shows seller items to a seller but not admin items", () => {
    const groups = visibleGroups({ isAdmin: false, isSeller: true });
    const labels = groups.flatMap((g) => g.items.map((i) => i.label));
    expect(labels).toContain("Seller");
    expect(labels).not.toContain("Usage");
    expect(labels).not.toContain("Decisions");
  });

  it("shows a support reviewer the Approvals inbox and nothing else from Admin", () => {
    const groups = visibleGroups({ isAdmin: false, isSeller: false, isStaff: true });
    const admin = groups.find((g) => g.label === "Admin");
    expect(admin?.items.map((i) => i.label)).toEqual(["Approvals"]);
  });

  it("hides Approvals from a customer", () => {
    const labels = visibleGroups({ isAdmin: false, isSeller: false, isStaff: false })
      .flatMap((g) => g.items.map((i) => i.label));
    expect(labels).not.toContain("Approvals");
  });

  it("shows admin items (Overview, Approvals, Usage) to an admin — no old labels", () => {
    const groups = visibleGroups({ isAdmin: true, isSeller: true });
    const labels = groups.flatMap((g) => g.items.map((i) => i.label));
    expect(labels).toContain("Overview");
    expect(labels).toContain("Approvals");
    expect(labels).toContain("Usage");
    expect(labels).toContain("Decisions");
    expect(labels).toContain("Runs"); // single Runs entry, from the Shop group
    expect(labels).toContain("Seller");
    expect(labels).not.toContain("Requests");
    expect(labels).not.toContain("Audit");
    // Only ONE "Runs" entry now (the admin /admin/audit duplicate was removed)
    expect(labels.filter((l) => l === "Runs")).toHaveLength(1);
  });
});

describe("labelForPath", () => {
  it("matches the most specific nav item", () => {
    expect(labelForPath("/admin/usage")).toBe("Usage");
    expect(labelForPath("/admin")).toBe("Overview");
    expect(labelForPath("/admin/decisions")).toBe("Decisions");
    expect(labelForPath("/runs")).toBe("Runs"); // the single user-facing runs page
  });

  it("matches nested detail routes to their parent item", () => {
    expect(labelForPath("/orders/abc-123")).toBe("Orders");
    expect(labelForPath("/products/p1")).toBe("Products");
    expect(labelForPath("/agents/product-discovery")).toBe("Agents");
  });

  it("falls back to Home for unknown paths", () => {
    expect(labelForPath("/totally-unknown")).toBe("Home");
  });
});
