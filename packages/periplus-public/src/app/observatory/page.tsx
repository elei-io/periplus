import type { Metadata } from "next"
import { CrawlerCockpit } from "@/components/crawler-cockpit"

export const metadata: Metadata = { title: "Observatory", description: "Follow new web observations, explore upcoming sites, and help shape where we look next." }

export default async function Page({ searchParams }: PageProps<"/observatory">) {
  const params = await searchParams
  const request = typeof params.request === "string" && /^[0-9a-f-]{36}$/i.test(params.request) ? params.request : undefined
  return <CrawlerCockpit key={request ?? "all"} initialId={request} />
}
