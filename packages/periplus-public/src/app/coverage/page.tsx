import type { Metadata } from "next"
import { hasWorkspaceInput, pageMetadata } from "@/lib/seo"
import { CrawlerCockpit } from "@/components/crawler-cockpit"

export async function generateMetadata({ searchParams }: PageProps<"/coverage">): Promise<Metadata> {
  const params = await searchParams
  return pageMetadata("/coverage", "Coverage", "Explore the sources in Periplus’s shared web databank, follow coverage requests, and request the data you need.", !hasWorkspaceInput(params, ["request", "description"]))
}

export default async function Page({ searchParams }: PageProps<"/coverage">) {
  const params = await searchParams
  const request = typeof params.request === "string" && /^[0-9a-f-]{36}$/i.test(params.request) ? params.request : undefined
  const description = typeof params.description === "string" ? params.description.slice(0, 2000) : undefined
  return <CrawlerCockpit key={request ?? description ?? "all"} initialId={request} initialDescription={description} />
}
