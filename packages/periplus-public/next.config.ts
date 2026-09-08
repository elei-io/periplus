import type { NextConfig } from "next"
import path from "node:path"

const nextConfig: NextConfig = {
  poweredByHeader: false,
  // Public, non-secret build identity, shared by static and dynamic metadata.
  env: { PERIPLUS_PUBLIC_ORIGIN: process.env.PERIPLUS_PUBLIC_ORIGIN ?? "" },
  output: "standalone",
  outputFileTracingRoot: path.join(import.meta.dirname, "../.."),
  headers: async () => [
    {
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      ],
    },
    { source: "/api/:path*", headers: [{ key: "X-Robots-Tag", value: "noindex, nofollow" }] },
  ],
}

export default nextConfig
