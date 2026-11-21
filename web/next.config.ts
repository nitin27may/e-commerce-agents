import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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
