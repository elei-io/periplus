import type { MetadataRoute } from "next"
import { indexablePaths, publicOrigin } from "@/lib/seo"

export default function sitemap(): MetadataRoute.Sitemap {
  return publicOrigin ? indexablePaths.map(path => ({ url: new URL(path, publicOrigin).href })) : []
}
