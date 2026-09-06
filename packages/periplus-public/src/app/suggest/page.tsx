import { SuggestPage } from "@/components/suggest-page"
import type { Metadata } from "next"
export const metadata: Metadata = { title: "Suggest coverage", description: "Help widen the shared view of the web. Request a URL or describe the data you need, and follow public coverage requests." }
export default async function Page({ searchParams }: PageProps<"/suggest">) {
  const params = await searchParams
  return <SuggestPage initialId={typeof params.request === "string" && /^[0-9a-f-]{36}$/i.test(params.request) ? params.request : undefined} />
}
