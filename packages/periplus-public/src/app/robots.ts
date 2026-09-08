import type { MetadataRoute } from "next"
import { publicOrigin } from "@/lib/seo"

export default function robots(): MetadataRoute.Robots {
  return publicOrigin
    ? { rules: { userAgent: "*", allow: "/", disallow: "/api/" }, sitemap: `${publicOrigin}/sitemap.xml` }
    : { rules: { userAgent: "*", disallow: "/" } }
}
