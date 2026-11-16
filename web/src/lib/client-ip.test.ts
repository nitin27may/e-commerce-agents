import { describe, expect, it } from "vitest";
import { trustedClientAddress, trustedProxyHops } from "./client-ip";

describe("trustedClientAddress", () => {
  it("trusts nothing with zero hops or no header", () => {
    expect(trustedClientAddress("1.2.3.4", 0)).toBeNull();
    expect(trustedClientAddress(null, 1)).toBeNull();
  });
  it("takes the entry hops places from the right", () => {
    expect(trustedClientAddress("9.9.9.9, 1.2.3.4", 1)).toBe("1.2.3.4");
    expect(trustedClientAddress("9.9.9.9, 1.2.3.4, 10.0.0.1", 2)).toBe("1.2.3.4");
  });
  it("refuses short chains and non-IP entries", () => {
    expect(trustedClientAddress("1.2.3.4", 2)).toBeNull();
    expect(trustedClientAddress("1.2.3.4, not-an-ip", 1)).toBeNull();
  });
  it("accepts IPv6", () => {
    expect(trustedClientAddress("x, 2001:db8::1", 1)).toBe("2001:db8::1");
    expect(trustedClientAddress("2001:db8::1", 1)).toBe("2001:db8::1");
  });
});

describe("trustedProxyHops", () => {
  it("parses positive integers and treats junk as 0", () => {
    expect(trustedProxyHops("2")).toBe(2);
    expect(trustedProxyHops("abc")).toBe(0);
    expect(trustedProxyHops("-1")).toBe(0);
    expect(trustedProxyHops(undefined)).toBe(0);
  });
});
