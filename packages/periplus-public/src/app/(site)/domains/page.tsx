import type { Metadata } from "next"

import { DomainsExplorer } from "@/components/catalogue/domains-explorer"
import { PageIntro } from "@/components/site/page-intro"
import { Badge } from "@/components/ui/badge"

export const metadata: Metadata = {
  title: "Domains",
  description: "Explore hostnames observed by Periplus.",
}

export default function DomainsPage() {
  return (
    <main className="mx-auto w-full max-w-7xl flex-1 space-y-8 px-4 py-12 sm:px-6 lg:px-8">
      <PageIntro
        eyebrow="Public explorer"
        title="Domains"
        description="Explore exact hostnames derived from durable URL observations. Subdomains remain separate, and observation time is evidence rather than a freshness guarantee."
        actions={<Badge variant="outline">Live catalogue</Badge>}
      />

      <DomainsExplorer />
    </main>
  )
}
