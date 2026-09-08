import type { Metadata } from "next"

export function parsePublicOrigin(value: string | undefined): string | undefined {
  if (!value?.trim()) return undefined
  const url = new URL(value)
  if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("PERIPLUS_PUBLIC_ORIGIN must be an HTTPS origin without a path, credentials, query, or fragment.")
  }
  return url.origin
}

// Build-time configuration: never derive search URLs from untrusted Host headers.
export const publicOrigin = parsePublicOrigin(process.env.PERIPLUS_PUBLIC_ORIGIN)
export const indexablePaths = ["/", "/about", "/docs", "/discover", "/sql", "/coverage"] as const

export function pageMetadata(path: string, title: string, description: string, index = true): Metadata {
  const url = publicOrigin ? new URL(path, publicOrigin).href : undefined
  const images = publicOrigin ? [{ url: `${publicOrigin}/share-image`, width: 1200, height: 630, alt: "Periplus — query the web as a dataset" }] : undefined
  return {
    title,
    description,
    alternates: url ? { canonical: url } : undefined,
    robots: { index: Boolean(publicOrigin) && index, follow: true },
    openGraph: { type: "website", siteName: "Periplus", title, description, url, images },
    twitter: { card: "summary_large_image", title, description, images },
  }
}

export function hasWorkspaceInput(params: Record<string, string | string[] | undefined>, keys: readonly string[]) {
  return keys.some(key => params[key] !== undefined)
}
