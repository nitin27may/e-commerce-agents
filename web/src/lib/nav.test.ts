import { describe, expect, it } from "vitest";
import { visibleGroups, labelForPath } from "./nav";

describe("visibleGroups", () => {
  it("shows shop + account to a plain customer, hides admin/seller", () => {
    const groups = visibleGroups({ isAdmin: false, isSeller: false });
    const labels = groups.flatMap((g) => g.items.map((i) => i.label));
    expect(labels).toContain("Home");
    expect(labels).toContain("Chat");
    // Customers get answers, not machinery: the agent catalog and runs are staff pages.
    expect(labels).not.toContain("Agents");
    expect(labels).not.toContain("Runs");
    expect(labels).not.toContain("Lab");
    expect(labels).toContain("Profile");
    expect(labels).not.toContain("Usage"); // adminOnly
    expect(labels).not.toContain("Seller dashboard"); // sellerOnly
    expect(groups.find((g) => g.label === "Seller")).toBeUndefined();
    expect(labels).not.toContain("Marketplace");
    expect(groups.find((g) => g.label === "Admin")).toBeUndefined();
  });

  it("keeps Profile alone under Account and puts seller pages under their own group", () => {
    const groups = visibleGroups({ isAdmin: false, isSeller: true });
    expect(groups.find((g) => g.label === "Account")?.items.map((i) => i.label)).toEqual(["Profile"]);
    expect(groups.find((g) => g.label === "Seller")?.items.map((i) => i.label)).toEqual(["Seller dashboard", "Proposals"]);
  });

  it("shows seller items to a seller but not admin items", () => {
    const groups = visibleGroups({ isAdmin: false, isSeller: true });
    const labels = groups.flatMap((g) => g.items.map((i) => i.label));
    expect(labels).toContain("Seller dashboard");
    expect(labels).not.toContain("Usage");
    expect(labels).not.toContain("Decisions");
  });

  it("shows a support reviewer the staff pages (Approvals, Runs, Agents, Lab) and nothing admin-only", () => {
    const groups = visibleGroups({ isAdmin: false, isSeller: false, isStaff: true });
    const admin = groups.find((g) => g.label === "Admin");
    expect(admin?.items.map((i) => i.label)).toEqual(["Approvals", "Runs", "Agents", "Lab"]);
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
    expect(labels).toContain("Runs"); // single Runs entry, a staff page in the Admin group
    expect(labels).toContain("Seller dashboard");
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
    expect(labelForPath("/runs")).toBe("Runs");
    expect(labelForPath("/seller/proposals")).toBe("Proposals");
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
