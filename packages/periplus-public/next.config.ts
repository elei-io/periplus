import { withPostHogConfig } from "@posthog/nextjs-config"
import type { NextConfig } from "next"
import { networkInterfaces } from "node:os"
import path from "node:path"

const nextConfig: NextConfig = {
  // Permit dev assets and HMR when opening this machine by its LAN address.
  allowedDevOrigins: Object.values(networkInterfaces()).flatMap((addresses) =>
    (addresses ?? []).filter(({ family }) => family === "IPv4").map(({ address }) => address),
  ),
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

export default process.env.POSTHOG_API_KEY && process.env.POSTHOG_PROJECT_ID
  ? withPostHogConfig(nextConfig, {
      personalApiKey: process.env.POSTHOG_API_KEY,
      projectId: process.env.POSTHOG_PROJECT_ID,
      host: process.env.POSTHOG_HOST ?? "https://us.posthog.com",
      sourcemaps: {
        enabled: true,
        releaseName: "periplus-public",
        releaseVersion: process.env.NEXT_PUBLIC_RELEASE ?? "local",
        deleteAfterUpload: true,
      },
    })
  : nextConfig
