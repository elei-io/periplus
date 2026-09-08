import type { Metadata } from "next"
import { hasWorkspaceInput, pageMetadata } from "@/lib/seo"
import { DiscoveryWorkspace } from "@/components/discovery-workspace"

export async function generateMetadata({ searchParams }: PageProps<"/discover">): Promise<Metadata> {
  const params = await searchParams
  return pageMetadata("/discover", "Discover", "Define your own dataset over collected web structure. Build and validate reusable SQL, inspect real records, and export the result.", !hasWorkspaceInput(params, ["question", "run"]))
}

export default async function DiscoverPage({ searchParams }: PageProps<"/discover">) {
  const params = await searchParams
  const question = typeof params.question === "string" ? params.question : undefined
  return <DiscoveryWorkspace key={question} question={question} autoRun={params.run === "1"} />
}
