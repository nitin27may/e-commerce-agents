import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Package } from "lucide-react";
import { PageContainer, PageHeader } from "./page-header";

describe("PageHeader", () => {
  it("renders the title as the page's h1", () => {
    render(<PageHeader title="Orders" />);
    expect(screen.getByRole("heading", { level: 1, name: "Orders" })).toBeInTheDocument();
  });

  it("renders the description and the actions slot", () => {
    render(
      <PageHeader
        title="Orders"
        description="Track and manage your orders."
        actions={<button type="button">Export</button>}
      />,
    );
    expect(screen.getByText("Track and manage your orders.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();
  });

  it("shows the icon tile only when an icon is given", () => {
    const { container, rerender } = render(<PageHeader title="Orders" />);
    expect(container.querySelector('[data-slot="page-header-icon"]')).toBeNull();
    rerender(<PageHeader title="Orders" icon={Package} />);
    const tile = container.querySelector('[data-slot="page-header-icon"]');
    expect(tile).not.toBeNull();
    expect(tile?.querySelector("svg")).toHaveAttribute("aria-hidden");
  });
});

describe("PageContainer", () => {
  it("uses the shared page width", () => {
    const { container } = render(<PageContainer>content</PageContainer>);
    const el = container.firstElementChild;
    expect(el).toHaveClass("mx-auto", "w-full", "max-w-6xl", "py-6");
    expect(el).toHaveTextContent("content");
  });
});
