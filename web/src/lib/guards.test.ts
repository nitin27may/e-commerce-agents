import { describe, expect, it } from "vitest";
import { asArray, asNumber, isRecord } from "./guards";

describe("guards", () => {
  it("asArray passes arrays through and turns anything else into []", () => {
    expect(asArray<number>([1, 2])).toEqual([1, 2]);
    expect(asArray(undefined)).toEqual([]);
    expect(asArray({ a: 1 })).toEqual([]);
    expect(asArray(null)).toEqual([]);
  });

  it("asNumber falls back for non-finite or non-number values", () => {
    expect(asNumber(4)).toBe(4);
    expect(asNumber("4")).toBe(0);
    expect(asNumber(NaN, 7)).toBe(7);
  });

  it("isRecord rejects arrays and null", () => {
    expect(isRecord({})).toBe(true);
    expect(isRecord([])).toBe(false);
    expect(isRecord(null)).toBe(false);
  });
});
