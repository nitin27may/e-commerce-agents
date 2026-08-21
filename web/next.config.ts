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
    remotePatterns: [
      { protocol: "https", hostname: "picsum.photos" },
      { protocol: "https", hostname: "fastly.picsum.photos" },
    ],
  },
};

export default nextConfig;
