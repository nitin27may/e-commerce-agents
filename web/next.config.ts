import type { NextConfig } from "next";

// The static preview (#301): `NEXT_PUBLIC_PREVIEW=1` exports plain files for GitHub Pages. It is the same
// app; src/lib/preview answers the API calls in the browser. Built by `pnpm build:preview`, which also sets
// PREVIEW_BASE_PATH (the Pages sub-path) and sets the API proxy route aside (a static export has no server).
const preview = process.env.NEXT_PUBLIC_PREVIEW === "1";

const nextConfig: NextConfig = preview
  ? {
      // Defined for both builds ("0" otherwise) so the bundler sees a literal and drops the preview code.
      env: { NEXT_PUBLIC_PREVIEW: "1" },
      output: "export",
      basePath: process.env.PREVIEW_BASE_PATH ?? "",
      trailingSlash: true,
      images: { unoptimized: true },
    }
  : {
      env: { NEXT_PUBLIC_PREVIEW: "0" },
      output: "standalone",
      images: {
        // Must match the hosts scripts/seed.py actually uses. These are stale in a way
        // that is currently invisible: product images render through plain <img>, which
        // ignores this list entirely, so the whitelist has been wrong since the seed data
        // moved to Unsplash and nothing failed. It would break the moment anyone switches
        // a product image to next/image.
        remotePatterns: [
          { protocol: "https", hostname: "images.unsplash.com" },
          { protocol: "https", hostname: "picsum.photos" },
          { protocol: "https", hostname: "fastly.picsum.photos" },
        ],
      },
    };

export default nextConfig;
