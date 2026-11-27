import { describe, expect, it } from "vitest";
import {
  discountPercent,
  formatCardDate,
  formatDeliveryWindow,
  humanizeEnum,
  monthLabels,
  shippingLabel,
  shortOrderId,
} from "./card-format";

describe("humanizeEnum", () => {
  it("sentence-cases backend slugs", () => {
    expect(humanizeEnum("flash_sale")).toBe("Flash sale");
    expect(humanizeEnum("bundle")).toBe("Bundle");
    expect(humanizeEnum("out_for_delivery")).toBe("Out for delivery");
    expect(humanizeEnum("central")).toBe("Central");
  });

  it("uses the hand-written labels where plain casing reads wrong", () => {
    expect(humanizeEnum("buy_x_get_y")).toBe("Buy X, get Y");
    expect(humanizeEnum("original_payment")).toBe("Original payment method");
  });

  it("leaves prose alone and treats missing values as empty", () => {
    expect(humanizeEnum("Shipped via UPS")).toBe("Shipped via UPS");
    expect(humanizeEnum(undefined)).toBe("");
  });
});

describe("shortOrderId", () => {
  it("keeps the first eight characters", () => {
    expect(shortOrderId("9dee7fee-d86b-5cca-a912-4987f224d562")).toBe("9dee7fee");
    expect(shortOrderId("")).toBe("");
  });
});

describe("shippingLabel", () => {
  it("drops a speed tier the carrier name already contains", () => {
    expect(shippingLabel("Standard Shipping", "standard")).toBe("Standard Shipping");
  });

  it("keeps a tier that adds information", () => {
    expect(shippingLabel("UPS", "overnight")).toBe("UPS (Overnight)");
    expect(shippingLabel("UPS")).toBe("UPS");
  });
});

describe("formatDeliveryWindow", () => {
  it("collapses a one-value range and singularises", () => {
    expect(formatDeliveryWindow("1-1 business days")).toBe("1 business day");
    expect(formatDeliveryWindow("3-3 days")).toBe("3 days");
  });

  it("passes real ranges and free text through", () => {
    expect(formatDeliveryWindow("5-7 business days")).toBe("5-7 business days");
    expect(formatDeliveryWindow("next business day")).toBe("next business day");
  });
});

describe("monthLabels", () => {
  it("shows month names for a series within one year", () => {
    expect(monthLabels(["2026-05", "2026-06", "2026-11"])).toEqual(["May", "Jun", "Nov"]);
  });

  it("adds the year when the series crosses one", () => {
    expect(monthLabels(["2025-12", "2026-01"])).toEqual(["Dec '25", "Jan '26"]);
  });

  it("passes non-month labels through", () => {
    expect(monthLabels(["Q1", "2026-02"])).toEqual(["Q1", "Feb"]);
  });
});

describe("formatCardDate", () => {
  it("formats an ISO day as that calendar day in any time zone", () => {
    expect(formatCardDate("2026-12-09")).toBe("Dec 9, 2026");
  });

  it("passes free text through instead of printing Invalid Date", () => {
    expect(formatCardDate("next Tuesday")).toBe("next Tuesday");
    expect(formatCardDate(undefined)).toBe("");
  });
});

describe("discountPercent", () => {
  it("rounds the saving to a whole percent", () => {
    expect(discountPercent(299.99, 349.99)).toBe(14);
  });

  it("is zero when there is no real discount", () => {
    expect(discountPercent(100, 100)).toBe(0);
    expect(discountPercent(100, 80)).toBe(0);
    expect(discountPercent(100, undefined)).toBe(0);
  });
});
