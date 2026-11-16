import { describe, expect, it } from "vitest";
import { isStaffRole } from "./roles";

describe("isStaffRole", () => {
  it("is true for support and admin", () => {
    expect(isStaffRole("support")).toBe(true);
    expect(isStaffRole("admin")).toBe(true);
  });

  it("is false for everyone else", () => {
    for (const role of ["customer", "power_user", "seller", "system", "", null, undefined]) {
      expect(isStaffRole(role)).toBe(false);
    }
  });
});
