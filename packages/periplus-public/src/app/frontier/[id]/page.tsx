import type { Metadata } from "next"
import { FrontierItemDetail } from "@/components/frontier-items"

export const metadata: Metadata = { title: "Observation details", description: "Current observation status and recorded lineage in Periplus.", robots: { index: false, follow: true } }

export default async function Page({ params }: PageProps<"/frontier/[id]">) { const { id } = await params; return <main className="discovery-workspace flex flex-col gap-5"><FrontierItemDetail id={id} /></main> }
