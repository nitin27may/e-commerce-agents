import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatSentimentCard } from "./sentiment-card";

describe("ChatSentimentCard", () => {
  it("renders with only analyze_sentiment's fields (no trend, no risk)", () => {
    render(
      <ChatSentimentCard
        data={{
          product_name: "Sony WH-1000XM5",
          overall_sentiment: "positive",
          average_rating: 4.7,
          total_reviews: 15,
          rating_distribution: { "5": 10, "4": 3, "3": 1, "2": 1, "1": 0 },
          pros: ["Great noise cancelling", "Comfortable"],
          cons: ["Expensive for what it is"],
        }}
      />
    );
    expect(screen.getByText("Sony WH-1000XM5")).toBeInTheDocument();
    expect(screen.getByText("Positive")).toBeInTheDocument();
    expect(screen.getByText("4.7")).toBeInTheDocument();
    expect(screen.getByText("from 15 reviews")).toBeInTheDocument();
    expect(screen.getByText("Great noise cancelling")).toBeInTheDocument();
    expect(screen.getByText("Expensive for what it is")).toBeInTheDocument();
    // A one-word "pro" says nothing to a shopper.
    expect(screen.queryByText("Comfortable")).not.toBeInTheDocument();
  });

  it("hides a pros list made only of single words, and shows one column for the list that remains", () => {
    render(
      <ChatSentimentCard
        data={{ product_name: "Dyson V15 Detect", average_rating: 4.1, pros: ["Great", "Best", "Fast"], cons: ["Loud at full power"] }}
      />
    );
    expect(screen.queryByText("Pros")).not.toBeInTheDocument();
    expect(screen.getByText("Loud at full power")).toBeInTheDocument();
  });

  it("renders the trend and risk badges when present", () => {
    render(
      <ChatSentimentCard
        data={{
          product_name: "Sony WH-1000XM5",
          trend: "declining",
          risk_level: "high",
          suspicious_count: 4,
        }}
      />
    );
    expect(screen.getByText("Declining")).toBeInTheDocument();
    expect(screen.getByText(/Many reviews look suspicious/)).toBeInTheDocument();
    expect(screen.getByText(/4 reviews flagged as potentially fake/)).toBeInTheDocument();
  });

  it("renders sparsely when only a product name is given", () => {
    render(<ChatSentimentCard data={{ product_name: "Sony WH-1000XM5" }} />);
    expect(screen.getByText("Sony WH-1000XM5")).toBeInTheDocument();
  });
});
