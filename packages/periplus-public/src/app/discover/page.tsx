import type { Metadata } from "next"
import { DiscoveryWorkspace } from "@/components/discovery-workspace"

export const metadata: Metadata = { title: "Discover", description: "Explore your own view of the web. Ask a question or write SQL against the shared Periplus corpus, then inspect the method and returned rows." }

export default async function DiscoverPage({ searchParams }: PageProps<"/discover">) {
  const params = await searchParams
  const question = typeof params.question === "string" ? params.question : undefined
  const sql = typeof params.sql === "string" ? params.sql : undefined
  const parameters = typeof params.parameters === "string" ? params.parameters : undefined
  return <DiscoveryWorkspace key={JSON.stringify([question, sql, parameters])} initialMode={params.mode === "sql" ? "sql" : "agent"} question={question} sql={sql} parameters={parameters} autoRun={params.run === "1"} />
}
