"""Review & Sentiment tools — reviews, sentiment analysis, fake detection, comparisons."""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.db import get_pool


@tool(name="get_product_reviews", description="Get paginated reviews for a product with sorting options.")
async def get_product_reviews(
    product_id: Annotated[str, Field(description="UUID of the product")],
    sort_by: Annotated[str | None, Field(description="Sort: newest, helpful, rating_high, rating_low")] = "newest",
    limit: Annotated[int, Field(description="Max reviews to return")] = 10,
    offset: Annotated[int, Field(description="Offset for pagination")] = 0,
) -> dict:
    pool = get_pool()
    order = {
        "newest": "r.created_at DESC",
        "helpful": "r.helpful_count DESC",
        "rating_high": "r.rating DESC, r.created_at DESC",
        "rating_low": "r.rating ASC, r.created_at DESC",
    }.get(sort_by or "newest", "r.created_at DESC")

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM reviews WHERE product_id = $1", product_id,
        )

        rows = await conn.fetch(
            f"""SELECT r.id, r.rating, r.title, r.body, r.verified_purchase,
                       r.helpful_count, r.is_flagged, r.created_at,
                       u.name as reviewer_name
                FROM reviews r
                JOIN users u ON r.user_id = u.id
                WHERE r.product_id = $1
                ORDER BY {order}
                LIMIT $2 OFFSET $3""",
            product_id, limit, offset,
        )

        product = await conn.fetchrow(
            "SELECT name, rating, review_count FROM products WHERE id = $1", product_id,
        )

        return {
            "product_id": product_id,
            "product_name": product["name"] if product else "Unknown",
            "overall_rating": float(product["rating"]) if product else None,
            "total_reviews": total,
            "showing": len(rows),
            "offset": offset,
            "reviews": [
                {
                    "id": str(r["id"]),
                    "rating": r["rating"],
                    "title": r["title"],
                    "body": r["body"],
                    "verified_purchase": r["verified_purchase"],
                    "helpful_count": r["helpful_count"],
                    "is_flagged": r["is_flagged"],
                    "reviewer": r["reviewer_name"],
                    "date": r["created_at"].isoformat(),
                }
                for r in rows
            ],
        }


@tool(name="analyze_sentiment", description="Aggregate sentiment analysis for a product: average rating, rating distribution, and pros/cons summary from review text.")
async def analyze_sentiment(
    product_id: Annotated[str, Field(description="UUID of the product to analyze")],
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        product = await conn.fetchrow(
            "SELECT name, rating, review_count FROM products WHERE id = $1", product_id,
        )
        if not product:
            return {"error": f"Product not found: {product_id}"}

        # Rating distribution
        dist = await conn.fetch(
            """SELECT rating, COUNT(*) as count
               FROM reviews WHERE product_id = $1
               GROUP BY rating ORDER BY rating DESC""",
            product_id,
        )
        distribution = {str(i): 0 for i in range(5, 0, -1)}
        total_reviews = 0
        for d in dist:
            distribution[str(d["rating"])] = d["count"]
            total_reviews += d["count"]

        # Verified vs unverified breakdown
        verified_count = await conn.fetchval(
            "SELECT COUNT(*) FROM reviews WHERE product_id = $1 AND verified_purchase = TRUE",
            product_id,
        )

        # Average by verified status
        verified_avg = await conn.fetchval(
            "SELECT AVG(rating) FROM reviews WHERE product_id = $1 AND verified_purchase = TRUE",
            product_id,
        )

        # Fetch reviews for keyword-based pros/cons extraction
        reviews = await conn.fetch(
            """SELECT rating, title, body FROM reviews
               WHERE product_id = $1
               ORDER BY helpful_count DESC
               LIMIT 50""",
            product_id,
        )

        # Simple keyword-based pros/cons extraction
        positive_keywords = ["great", "excellent", "love", "perfect", "amazing", "best", "quality", "fast", "comfortable", "worth"]
        negative_keywords = ["poor", "bad", "terrible", "broken", "slow", "cheap", "disappointed", "waste", "defective", "flimsy"]

        pros = []
        cons = []
        for r in reviews:
            text = f"{r['title'] or ''} {r['body']}".lower()
            if r["rating"] >= 4:
                for kw in positive_keywords:
                    if kw in text and kw not in [p.lower() for p in pros]:
                        pros.append(kw.capitalize())
            if r["rating"] <= 2:
                for kw in negative_keywords:
                    if kw in text and kw not in [c.lower() for c in cons]:
                        cons.append(kw.capitalize())

        # Sentiment label
        avg = float(product["rating"])
        if avg >= 4.5:
            sentiment = "very_positive"
        elif avg >= 3.5:
            sentiment = "positive"
        elif avg >= 2.5:
            sentiment = "mixed"
        elif avg >= 1.5:
            sentiment = "negative"
        else:
            sentiment = "very_negative"

        return {
            "product_id": product_id,
            "product_name": product["name"],
            "overall_sentiment": sentiment,
            "average_rating": avg,
            "total_reviews": total_reviews,
            "rating_distribution": distribution,
            "verified_reviews": verified_count,
            "unverified_reviews": total_reviews - verified_count,
            "verified_avg_rating": round(float(verified_avg), 2) if verified_avg else None,
            "pros": pros[:5],
            "cons": cons[:5],
        }


@tool(name="get_sentiment_by_topic", description="Break down reviews into topics (quality, value, shipping, design, durability) with mention counts and average rating per topic.")
async def get_sentiment_by_topic(
    product_id: Annotated[str, Field(description="UUID of the product")],
) -> dict:
    pool = get_pool()

    # Topic keyword mappings
    topic_keywords: dict[str, list[str]] = {
        "quality": ["quality", "well-made", "well made", "craftsmanship", "build", "material", "sturdy", "solid", "premium"],
        "value": ["value", "price", "worth", "money", "expensive", "cheap", "affordable", "overpriced", "bargain", "deal"],
        "shipping": ["shipping", "delivery", "arrived", "package", "packaging", "shipped", "transit", "late", "fast delivery"],
        "design": ["design", "look", "style", "aesthetic", "beautiful", "color", "colour", "sleek", "modern", "appearance"],
        "durability": ["durable", "durability", "lasting", "broke", "broken", "wear", "tear", "fragile", "robust", "lifespan"],
    }

    async with pool.acquire() as conn:
        product = await conn.fetchrow(
            "SELECT name FROM products WHERE id = $1", product_id,
        )
        if not product:
            return {"error": f"Product not found: {product_id}"}

        reviews = await conn.fetch(
            "SELECT rating, title, body FROM reviews WHERE product_id = $1",
            product_id,
        )

        topics: dict[str, dict] = {}
        for topic, keywords in topic_keywords.items():
            mentions = 0
            ratings = []
            for r in reviews:
                text = f"{r['title'] or ''} {r['body']}".lower()
                if any(kw in text for kw in keywords):
                    mentions += 1
                    ratings.append(r["rating"])
            avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else None
            topics[topic] = {
                "mentions": mentions,
                "average_rating": avg_rating,
                "sentiment": (
                    "positive" if avg_rating and avg_rating >= 3.5
                    else "negative" if avg_rating and avg_rating < 2.5
                    else "mixed" if avg_rating
                    else "no_data"
                ),
            }

        return {
            "product_id": product_id,
            "product_name": product["name"],
            "total_reviews_analyzed": len(reviews),
            "topics": topics,
        }


@tool(name="get_sentiment_trend", description="Track sentiment over time with monthly average ratings for a product.")
async def get_sentiment_trend(
    product_id: Annotated[str, Field(description="UUID of the product")],
    months: Annotated[int, Field(description="Number of months to look back")] = 6,
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        product = await conn.fetchrow(
            "SELECT name FROM products WHERE id = $1", product_id,
        )
        if not product:
            return {"error": f"Product not found: {product_id}"}

        rows = await conn.fetch(
            """SELECT DATE_TRUNC('month', created_at) as month,
                      AVG(rating) as avg_rating,
                      COUNT(*) as review_count
               FROM reviews
               WHERE product_id = $1
                 AND created_at >= NOW() - ($2 || ' months')::interval
               GROUP BY DATE_TRUNC('month', created_at)
               ORDER BY month ASC""",
            product_id, str(months),
        )

        trend_data = [
            {
                "month": r["month"].strftime("%Y-%m"),
                "average_rating": round(float(r["avg_rating"]), 2),
                "review_count": r["review_count"],
            }
            for r in rows
        ]

        # Calculate trend direction
        if len(trend_data) >= 2:
            first_half = trend_data[:len(trend_data) // 2]
            second_half = trend_data[len(trend_data) // 2:]
            first_avg = sum(t["average_rating"] for t in first_half) / len(first_half)
            second_avg = sum(t["average_rating"] for t in second_half) / len(second_half)
            if second_avg > first_avg + 0.2:
                trend = "improving"
            elif second_avg < first_avg - 0.2:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"

        return {
            "product_id": product_id,
            "product_name": product["name"],
            "period_months": months,
            "trend": trend,
            "monthly_data": trend_data,
        }


@tool(name="detect_fake_reviews", description="Detect potentially fake or suspicious reviews for a product. Checks flagged reviews, unverified 5-star ratings, and generic language patterns.")
async def detect_fake_reviews(
    product_id: Annotated[str, Field(description="UUID of the product to check")],
) -> dict:
    pool = get_pool()

    # Generic language patterns common in fake reviews
    generic_patterns = [
        "great product",
        "highly recommend",
        "five stars",
        "5 stars",
        "best product ever",
        "love it",
        "amazing product",
        "perfect product",
        "must buy",
        "worth every penny",
    ]

    async with pool.acquire() as conn:
        product = await conn.fetchrow(
            "SELECT name FROM products WHERE id = $1", product_id,
        )
        if not product:
            return {"error": f"Product not found: {product_id}"}

        total = await conn.fetchval(
            "SELECT COUNT(*) FROM reviews WHERE product_id = $1", product_id,
        )

        # Already flagged reviews
        flagged = await conn.fetch(
            """SELECT r.id, r.rating, r.title, r.body, r.verified_purchase,
                      r.created_at, u.name as reviewer_name
               FROM reviews r
               JOIN users u ON r.user_id = u.id
               WHERE r.product_id = $1 AND r.is_flagged = TRUE
               ORDER BY r.created_at DESC""",
            product_id,
        )

        # Unverified 5-star reviews (high suspicion)
        unverified_five_star = await conn.fetch(
            """SELECT r.id, r.rating, r.title, r.body, r.created_at,
                      u.name as reviewer_name
               FROM reviews r
               JOIN users u ON r.user_id = u.id
               WHERE r.product_id = $1
                 AND r.verified_purchase = FALSE
                 AND r.rating = 5
                 AND r.is_flagged = FALSE
               ORDER BY r.created_at DESC
               LIMIT 20""",
            product_id,
        )

        # Check for generic language in all reviews
        all_reviews = await conn.fetch(
            """SELECT r.id, r.rating, r.title, r.body, r.verified_purchase,
                      r.created_at, u.name as reviewer_name
               FROM reviews r
               JOIN users u ON r.user_id = u.id
               WHERE r.product_id = $1 AND r.is_flagged = FALSE
               ORDER BY r.created_at DESC""",
            product_id,
        )

        generic_matches = []
        for r in all_reviews:
            text = f"{r['title'] or ''} {r['body']}".lower()
            matched_patterns = [p for p in generic_patterns if p in text]
            # Short body + generic patterns + high rating = suspicious
            if matched_patterns and len(r["body"]) < 100 and r["rating"] >= 4:
                generic_matches.append({
                    "review_id": str(r["id"]),
                    "rating": r["rating"],
                    "title": r["title"],
                    "body_preview": r["body"][:100],
                    "verified_purchase": r["verified_purchase"],
                    "matched_patterns": matched_patterns,
                    "reason": "Short review with generic language",
                    "reviewer": r["reviewer_name"],
                    "date": r["created_at"].isoformat(),
                })

        suspicious_count = len(flagged) + len(unverified_five_star) + len(generic_matches)

        return {
            "product_id": product_id,
            "product_name": product["name"],
            "total_reviews": total,
            "suspicious_count": suspicious_count,
            "risk_level": (
                "high" if suspicious_count > total * 0.3 and total > 0
                else "medium" if suspicious_count > total * 0.1 and total > 0
                else "low"
            ),
            "flagged_reviews": [
                {
                    "review_id": str(r["id"]),
                    "rating": r["rating"],
                    "title": r["title"],
                    "body_preview": r["body"][:100],
                    "verified_purchase": r["verified_purchase"],
                    "reason": "Previously flagged as suspicious",
                    "reviewer": r["reviewer_name"],
                    "date": r["created_at"].isoformat(),
                }
                for r in flagged
            ],
            "unverified_five_star": [
                {
                    "review_id": str(r["id"]),
                    "title": r["title"],
                    "body_preview": r["body"][:100],
                    "reason": "Unverified purchase with 5-star rating",
                    "reviewer": r["reviewer_name"],
                    "date": r["created_at"].isoformat(),
                }
                for r in unverified_five_star
            ],
            "generic_language_matches": generic_matches[:10],
        }


@tool(name="search_reviews", description="Search reviews for a product by keyword in the review title or body.")
async def search_reviews(
    product_id: Annotated[str, Field(description="UUID of the product")],
    keyword: Annotated[str, Field(description="Keyword to search in review title and body")],
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        product = await conn.fetchrow(
            "SELECT name FROM products WHERE id = $1", product_id,
        )
        if not product:
            return {"error": f"Product not found: {product_id}"}

        rows = await conn.fetch(
            """SELECT r.id, r.rating, r.title, r.body, r.verified_purchase,
                      r.helpful_count, r.created_at, u.name as reviewer_name
               FROM reviews r
               JOIN users u ON r.user_id = u.id
               WHERE r.product_id = $1
                 AND (r.title ILIKE $2 OR r.body ILIKE $2)
               ORDER BY r.helpful_count DESC, r.created_at DESC
               LIMIT 20""",
            product_id, f"%{keyword}%",
        )

        return {
            "product_id": product_id,
            "product_name": product["name"],
            "keyword": keyword,
            "matches": len(rows),
            "reviews": [
                {
                    "id": str(r["id"]),
                    "rating": r["rating"],
                    "title": r["title"],
                    "body": r["body"],
                    "verified_purchase": r["verified_purchase"],
                    "helpful_count": r["helpful_count"],
                    "reviewer": r["reviewer_name"],
                    "date": r["created_at"].isoformat(),
                }
                for r in rows
            ],
        }


@tool(name="draft_seller_response", description="Generate a professional response template for a negative review. Returns a template the seller can customize.")
async def draft_seller_response(
    review_id: Annotated[str, Field(description="UUID of the review to respond to")],
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT r.id, r.rating, r.title, r.body, r.created_at,
                      u.name as reviewer_name, p.name as product_name
               FROM reviews r
               JOIN users u ON r.user_id = u.id
               JOIN products p ON r.product_id = p.id
               WHERE r.id = $1""",
            review_id,
        )
        if not row:
            return {"error": f"Review not found: {review_id}"}

        reviewer = row["reviewer_name"]
        product_name = row["product_name"]
        rating = row["rating"]

        # Select template based on rating
        if rating <= 2:
            template = (
                f"Dear {reviewer},\n\n"
                f"Thank you for taking the time to share your feedback about the {product_name}. "
                f"We sincerely apologize that your experience did not meet your expectations.\n\n"
                f"We take all feedback seriously and would like the opportunity to make this right. "
                f"Could you please reach out to our customer support team so we can investigate "
                f"your concern and find a suitable resolution?\n\n"
                f"We appreciate your patience and look forward to resolving this for you.\n\n"
                f"Best regards,\n[Your Name]\nAgentBazaar Seller Support"
            )
        elif rating == 3:
            template = (
                f"Dear {reviewer},\n\n"
                f"Thank you for your honest review of the {product_name}. "
                f"We appreciate you highlighting both the positives and areas for improvement.\n\n"
                f"Your feedback helps us enhance our products and service. "
                f"If there is anything specific we can do to improve your experience, "
                f"please do not hesitate to contact our support team.\n\n"
                f"Thank you for choosing AgentBazaar.\n\n"
                f"Best regards,\n[Your Name]\nAgentBazaar Seller Support"
            )
        else:
            template = (
                f"Dear {reviewer},\n\n"
                f"Thank you for your review of the {product_name}! "
                f"We are glad to hear about your experience.\n\n"
                f"If there is anything else we can help with, please let us know.\n\n"
                f"Best regards,\n[Your Name]\nAgentBazaar Seller Support"
            )

        return {
            "review_id": str(row["id"]),
            "product_name": product_name,
            "reviewer": reviewer,
            "rating": rating,
            "review_title": row["title"],
            "review_body": row["body"][:200],
            "response_template": template,
            "note": "This is a template. Customize it with specific details about the customer's concern before sending.",
        }


@tool(name="compare_product_reviews", description="Compare review metrics (average rating, review count, sentiment) across 2-3 products.")
async def compare_product_reviews(
    product_ids: Annotated[list[str], Field(description="List of 2-3 product UUIDs to compare")],
) -> dict:
    if len(product_ids) < 2 or len(product_ids) > 3:
        return {"error": "Please provide 2-3 product IDs to compare"}

    pool = get_pool()
    comparisons = []
    async with pool.acquire() as conn:
        for pid in product_ids:
            product = await conn.fetchrow(
                "SELECT name, rating, review_count FROM products WHERE id = $1", pid,
            )
            if not product:
                comparisons.append({"product_id": pid, "error": "Product not found"})
                continue

            # Rating distribution
            dist = await conn.fetch(
                """SELECT rating, COUNT(*) as count
                   FROM reviews WHERE product_id = $1
                   GROUP BY rating ORDER BY rating DESC""",
                pid,
            )
            distribution = {str(i): 0 for i in range(5, 0, -1)}
            for d in dist:
                distribution[str(d["rating"])] = d["count"]

            # Verified vs unverified
            verified = await conn.fetchval(
                "SELECT COUNT(*) FROM reviews WHERE product_id = $1 AND verified_purchase = TRUE",
                pid,
            )

            # Recent trend (last 3 months avg)
            recent_avg = await conn.fetchval(
                """SELECT AVG(rating) FROM reviews
                   WHERE product_id = $1 AND created_at >= NOW() - INTERVAL '3 months'""",
                pid,
            )

            avg = float(product["rating"])
            if avg >= 4.5:
                sentiment = "very_positive"
            elif avg >= 3.5:
                sentiment = "positive"
            elif avg >= 2.5:
                sentiment = "mixed"
            elif avg >= 1.5:
                sentiment = "negative"
            else:
                sentiment = "very_negative"

            comparisons.append({
                "product_id": pid,
                "product_name": product["name"],
                "average_rating": avg,
                "review_count": product["review_count"],
                "sentiment": sentiment,
                "rating_distribution": distribution,
                "verified_reviews": verified,
                "recent_avg_rating": round(float(recent_avg), 2) if recent_avg else None,
            })

    return {"comparisons": comparisons}
