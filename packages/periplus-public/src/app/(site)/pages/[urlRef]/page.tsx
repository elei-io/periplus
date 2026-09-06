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
  title: "Page details",
}

export default async function PageDetailPage({
  params,
}: {
  params: Promise<{ urlRef: string }>
}) {
  const { urlRef } = await params

  return (
    <main className="mx-auto w-full max-w-7xl flex-1 space-y-8 px-4 py-12 sm:px-6 lg:px-8">
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink render={<Link href="/pages" />}>
              Pages
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>{urlRef}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      <PageIntro
        eyebrow="Observed URL"
        title="Page evidence"
        description={`Route reference: ${urlRef}. The final reversible URL-reference encoding will be defined with the public page API.`}
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatPlaceholder label="First observed" description="Earliest retained evidence" />
        <StatPlaceholder label="Last observed" description="Most recent retained evidence" />
        <StatPlaceholder label="Observations" description="Complete URL history" />
        <StatPlaceholder label="Content versions" description="Distinct immutable objects" />
      </div>

      <CatalogueEmptyState
        title="Page history is not connected yet"
        description="Outcomes, redirects, content versions, and outgoing links will be rendered from a bounded exact-URL history endpoint."
      />
    </main>
  )
}
