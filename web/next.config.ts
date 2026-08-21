import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // NEXT_PUBLIC_* is inlined into the compiled bundle, so two dev servers
  // pointed at different backends must not share a build directory — the
  // second one starts in milliseconds off the first one's cache and silently
  // serves the first one's API URL. That is how a dual-backend parity run
  // ends up testing the same backend twice. Unset in normal use.
  ...(process.env.NEXT_DIST_DIR ? { distDir: process.env.NEXT_DIST_DIR } : {}),
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
