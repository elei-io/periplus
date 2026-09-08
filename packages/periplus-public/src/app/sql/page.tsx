import type { Metadata } from "next"
import { hasWorkspaceInput, pageMetadata } from "@/lib/seo"
import { QueryWorkbench } from "@/components/query-workbench"

export async function generateMetadata({ searchParams }: PageProps<"/sql">): Promise<Metadata> {
  const params = await searchParams
  return pageMetadata("/sql", "SQL", "Write and run SQL over Periplus's public web catalogue.", !hasWorkspaceInput(params, ["sql", "parameters", "run"]))
}

export default async function SqlPage({ searchParams }: PageProps<"/sql">) {
  const params = await searchParams
  const sql = typeof params.sql === "string" ? params.sql : undefined
  const parameters = typeof params.parameters === "string" ? params.parameters : undefined
  return <main className="discovery-workspace">
    <header className="flex flex-wrap items-baseline justify-between gap-2 py-6"><h1 className="text-xl font-medium">SQL console</h1><p className="text-sm text-muted-foreground">Explore the catalogue, write SQL, and inspect results.</p></header>
    <QueryWorkbench key={JSON.stringify([sql, parameters])} initialSql={sql} initialParameters={parameters} autoRun={params.run === "1"} />
  </main>
}
