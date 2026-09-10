import type { Metadata } from "next"
import { hasWorkspaceInput, pageMetadata } from "@/lib/seo"
import { CrawlerCockpit } from "@/components/crawler-cockpit"

export async function generateMetadata({ searchParams }: PageProps<"/coverage">): Promise<Metadata> {
  const params = await searchParams
  return pageMetadata("/coverage", "Coverage", "See which websites people are adding to Periplus. Contribute sources for everyone to research and follow collection progress.", !hasWorkspaceInput(params, ["request", "description"]))
}

export default async function Page({ searchParams }: PageProps<"/coverage">) {
  const params = await searchParams
  const request = typeof params.request === "string" && /^[0-9a-f-]{36}$/i.test(params.request) ? params.request : undefined
  const description = typeof params.description === "string" ? params.description.slice(0, 2000) : undefined
  return <CrawlerCockpit key={request ?? description ?? "all"} initialId={request} initialDescription={description} />
}
