import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GroundingBadge } from "./grounding-badge";
import type { GroundingReport } from "@/lib/api";

const ALL_VERIFIED: GroundingReport = {
  total: 2,
  verified: 2,
  unverified: 0,
  claims: [
    { type: "product", id: "0fd372fa-ecb2-4db0-bb71-8628a784ced9", status: "verified", detail: null, source: "ledger" },
    { type: "order", id: "1a2b3c4d-5e6f-7890-abcd-ef0123456789", status: "verified", detail: null, source: "db" },
  ],
};

const MIXED: GroundingReport = {
  total: 2,
  verified: 1,
  unverified: 1,
  claims: [
    { type: "product", id: "0fd372fa-ecb2-4db0-bb71-8628a784ced9", status: "verified", detail: null, source: "ledger" },
    {
      type: "product",
      id: "99999999-9999-9999-9999-999999999999",
      status: "not_found",
      detail: "no product with this id exists",
      source: "db",
    },
  ],
};

describe("GroundingBadge", () => {
  it("renders nothing when there's no report", () => {
    const { container } = render(<GroundingBadge report={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when the report has zero claims", () => {
    const { container } = render(<GroundingBadge report={{ total: 0, verified: 0, unverified: 0, claims: [] }} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the verified count in the collapsed summary", () => {
    render(<GroundingBadge report={ALL_VERIFIED} />);
    expect(screen.getByText(/2 facts verified against the database/)).toBeInTheDocument();
    expect(screen.queryByText(/unverified/)).not.toBeInTheDocument();
  });

  it("mentions the unverified count when some claims failed", () => {
    render(<GroundingBadge report={MIXED} />);
    expect(screen.getByText(/1 fact verified against the database, 1 unverified/)).toBeInTheDocument();
  });

  it("expands to show per-claim detail on click", () => {
    render(<GroundingBadge report={MIXED} />);
    expect(screen.queryByText("no product with this id exists")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText("no product with this id exists")).toBeInTheDocument();
    expect(screen.getByText("not found — stripped")).toBeInTheDocument();
    expect(screen.getByText("verified")).toBeInTheDocument();
  });
});
