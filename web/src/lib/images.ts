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
 * Seed products whose stored image shows something else (a Kindle shown as two
 * iPads, a Roomba as a phone). The seed data and its image URLs are shared with
 * recorded fixtures and must not change, so the storefront swaps the photo here.
 *
 * Matched by product name, and also by the seed photo id so callers that only
 * have the image URL (chat cards) get the same fix.
 */
const PRODUCT_IMAGE_OVERRIDES: ReadonlyArray<{
  name: string;
  seedPhoto: string;
  url: string;
}> = [
  {
    name: "Dyson V15 Detect",
    seedPhoto: "photo-1563453392212-326f5e854473",
    url: "https://images.unsplash.com/photo-1765970101634-9cb341a94ea0?w=400&h=400&fit=crop",
  },
  {
    name: "Kindle Paperwhite Signature",
    seedPhoto: "photo-1585790050230-5dd28404ccb9",
    url: "https://images.unsplash.com/photo-1555631545-9dc21031669b?w=400&h=400&fit=crop",
  },
  {
    name: "Samsung T7 Shield SSD 2TB",
    seedPhoto: "photo-1597872200969-2b65d56bd16b",
    url: "https://images.unsplash.com/photo-1779896412214-52031d27211a?w=400&h=400&fit=crop",
  },
  {
    name: "Anker 737 Power Bank",
    seedPhoto: "photo-1609091839311-d5365f9ff1c5",
    url: "https://images.unsplash.com/photo-1760708825913-65a50b3dc39b?w=400&h=400&fit=crop",
  },
  {
    name: "iRobot Roomba j9+",
    seedPhoto: "photo-1589894404892-7310b92ea7a2",
    url: "https://images.unsplash.com/photo-1653990480360-31a12ce9723e?w=400&h=400&fit=crop",
  },
  {
    name: "Philips Hue Starter Kit",
    seedPhoto: "photo-1558171813-4c088753af8f",
    url: "https://images.unsplash.com/photo-1712294252418-680891540aa8?w=400&h=400&fit=crop",
  },
  {
    name: "North Face Thermoball Eco Jacket",
    seedPhoto: "photo-1551028719-00167b16eac5",
    url: "https://images.unsplash.com/photo-1557418669-db3f781a58c0?w=400&h=400&fit=crop",
  },
  {
    name: "Uniqlo Ultra Light Down Jacket",
    seedPhoto: "photo-1539533018447-63fcce2678e3",
    url: "https://images.unsplash.com/photo-1706765779494-2705542ebe74?w=400&h=400&fit=crop",
  },
];

const OVERRIDE_BY_NAME = new Map(
  PRODUCT_IMAGE_OVERRIDES.map((o) => [o.name.toLowerCase(), o.url]),
);
const OVERRIDE_BY_SEED_PHOTO = new Map(
  PRODUCT_IMAGE_OVERRIDES.map((o) => [o.seedPhoto, o.url]),
);

function seedPhotoId(imageUrl: string): string | undefined {
  return imageUrl.match(/photo-[\w-]+/)?.[0];
}

/** The replacement photo for a product whose seed image is wrong, if any. */
export function productImageOverride(
  name?: string | null,
  imageUrl?: string | null,
): string | undefined {
  if (name) {
    const byName = OVERRIDE_BY_NAME.get(name.trim().toLowerCase());
    if (byName) return byName;
  }
  if (imageUrl) {
    const id = seedPhotoId(imageUrl);
    if (id) return OVERRIDE_BY_SEED_PHOTO.get(id);
  }
  return undefined;
}

/**
 * Returns an image URL for a product.
 *
 * Priority:
 *  1. A verified override for a product whose seed photo is wrong
 *  2. `imageUrl` — stored on the product record (Unsplash URL from seed)
 *  3. `category` — curated category fallback
 *  4. Generic product fallback
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
  name?: string | null,
): string {
  const override = productImageOverride(name, imageUrl);
  if (override) {
    return override
      .replace(/w=\d+/, `w=${width}`)
      .replace(/h=\d+/, `h=${height}`);
  }

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
