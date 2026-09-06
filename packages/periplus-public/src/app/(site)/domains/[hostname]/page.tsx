import Link from "next/link"
import type { Metadata } from "next"

import { CatalogueEmptyState } from "@/components/catalogue/catalogue-empty-state"
import { StatPlaceholder } from "@/components/catalogue/stat-placeholder"
import { PageIntro } from "@/components/site/page-intro"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"

export const metadata: Metadata = {
  title: "Domain details",
}

export default async function DomainDetailPage({
  params,
}: {
  params: Promise<{ hostname: string }>
}) {
  const { hostname } = await params

  return (
    <main className="mx-auto w-full max-w-7xl flex-1 space-y-8 px-4 py-12 sm:px-6 lg:px-8">
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink render={<Link href="/domains" />}>
              Domains
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>{hostname}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      <PageIntro
        eyebrow="Observed hostname"
        title={hostname}
        description="This page will summarize durable observations for one exact normalized hostname. Subdomains remain separate in the first public contract."
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatPlaceholder label="Known URLs" description="Distinct observed URLs" />
        <StatPlaceholder label="Observations" description="Terminal URL observations" />
        <StatPlaceholder label="Last observed" description="Most recent evidence time" />
        <StatPlaceholder label="With content" description="Observations retaining bytes" />
      </div>

      <CatalogueEmptyState
        title="Observation history is not connected yet"
        description="Recent pages, outcomes, media types, and observation history will be added after the public hostname API is bounded and verified."
      />
    </main>
  )
}
