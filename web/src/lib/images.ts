// ---------------------------------------------------------------------------
// Product image helpers
// ---------------------------------------------------------------------------

/** Curated Unsplash URLs keyed by category for fallback images */
const CATEGORY_FALLBACK_IMAGES: Record<string, string> = {
  electronics:
    "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=400&h=400&fit=crop",
  clothing:
    "https://images.unsplash.com/photo-1551028719-00167b16eac5?w=400&h=400&fit=crop",
  home: "https://images.unsplash.com/photo-1556909114-f6e7ad7d3136?w=400&h=400&fit=crop",
  sports:
    "https://images.unsplash.com/photo-1534438327276-14e5300c3a48?w=400&h=400&fit=crop",
  books:
    "https://images.unsplash.com/photo-1512820790803-83ca734da794?w=400&h=400&fit=crop",
};

const DEFAULT_FALLBACK =
  "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=400&h=400&fit=crop";

/**
 * Returns an image URL for a product.
 *
 * Priority:
 *  1. `imageUrl` — stored on the product record (Unsplash URL from seed)
 *  2. `category` — curated category fallback
 *  3. Generic product fallback
 *
 * The `width` and `height` params are appended to Unsplash URLs when an
 * explicit imageUrl is provided (replacing any existing w/h query params).
 */
export function productImageUrl(
  productId: string,
  width = 400,
  height = 300,
  imageUrl?: string | null,
  category?: string | null,
): string {
  if (imageUrl) {
    // Replace w= and h= query params with requested dimensions
    return imageUrl
      .replace(/w=\d+/, `w=${width}`)
      .replace(/h=\d+/, `h=${height}`);
  }

  if (category) {
    const fallback = CATEGORY_FALLBACK_IMAGES[category.toLowerCase()];
    if (fallback) {
      return fallback
        .replace(/w=\d+/, `w=${width}`)
        .replace(/h=\d+/, `h=${height}`);
    }
  }

  return DEFAULT_FALLBACK
    .replace(/w=\d+/, `w=${width}`)
    .replace(/h=\d+/, `h=${height}`);
}

/**
 * Returns a curated fallback image for a product category.
 */
export function categoryImageUrl(category: string, width = 400, height = 400): string {
  const fallback =
    CATEGORY_FALLBACK_IMAGES[category.toLowerCase()] || DEFAULT_FALLBACK;
  return fallback
    .replace(/w=\d+/, `w=${width}`)
    .replace(/h=\d+/, `h=${height}`);
}
