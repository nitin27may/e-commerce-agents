import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Kept, but no longer load-bearing. This existed because the backend
  // address was a NEXT_PUBLIC_* variable compiled into the bundle, so two dev
  // servers pointed at different backends sharing a build directory would
  // silently serve the same API URL — which is how a dual-backend parity run
  // tested the same backend twice. The address is now ORCHESTRATOR_URL, read
  // per request by src/app/api/[...path]/route.ts, so a build encodes nothing
  // about the backend and two dev servers can share a cache safely. Still
  // useful for keeping concurrent builds from fighting. Unset in normal use.
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
