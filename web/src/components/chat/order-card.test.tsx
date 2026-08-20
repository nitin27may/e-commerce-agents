import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatOrderCard } from "./order-card";

describe("ChatOrderCard", () => {
  it("renders an object-shaped shipping_address instead of crashing (Phase 8.2)", () => {
    // The DB's native shape is JSONB — chat-schemas.ts's OrderDataSchema accepts
    // this union, but the component used to type shipping_address as string-only
    // and render it directly as a child, which throws "Objects are not valid as
    // a React child" the moment an agent emits the unstringified object form.
    const data = {
      id: "48bfb7a1-0b02-4c89-94c9-552d629aaa92",
      status: "shipped",
      shipping_address: { street: "123 Main St", city: "Springfield", state: "IL", zip: "62704" },
    };
    render(<ChatOrderCard data={data} />);
    expect(screen.getByText("123 Main St, Springfield, IL, 62704")).toBeInTheDocument();
  });

  it("still renders a plain string shipping_address", () => {
    const data = { id: "order-1", shipping_address: "456 Oak Ave, Metropolis" };
    render(<ChatOrderCard data={data} />);
    expect(screen.getByText("456 Oak Ave, Metropolis")).toBeInTheDocument();
  });

  it("omits the address row when shipping_address is null", () => {
    const data = { id: "order-1", shipping_address: null as unknown as undefined };
    render(<ChatOrderCard data={data} />);
    expect(screen.queryByText(/Main St/)).not.toBeInTheDocument();
  });

  it("does not render NaN when an item has no unit_price or quantity", () => {
    const data = {
      id: "order-1",
      items: [{ name: "Mystery Item" }],
    };
    render(<ChatOrderCard data={data} />);
    expect(screen.getByText("Mystery Item")).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });

  it("falls back to a timeline event's label when status is absent", () => {
    const data = {
      id: "order-1",
      timeline: [{ label: "Order placed", date: "2026-01-01" }],
    };
    render(<ChatOrderCard data={data} />);
    expect(screen.getByText("Order placed")).toBeInTheDocument();
  });
});
