import type { Metadata } from "next"
import { DiscoveryWorkspace } from "@/components/discovery-workspace"

export const metadata: Metadata = { title: "Discover", description: "Define your own dataset over collected web structure. Build and validate reusable SQL, inspect real records, and export the result." }

export default async function DiscoverPage({ searchParams }: PageProps<"/discover">) {
  const params = await searchParams
  const question = typeof params.question === "string" ? params.question : undefined
  return <DiscoveryWorkspace key={question} question={question} autoRun={params.run === "1"} />
}
