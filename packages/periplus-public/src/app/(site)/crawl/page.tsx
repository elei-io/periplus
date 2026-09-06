import { CrawlRequest } from "@/components/crawls/crawl-request"

export default async function CrawlPage({ searchParams }: PageProps<"/crawl">) {
  const { receipt } = await searchParams
  return <main className="mx-auto w-full max-w-3xl px-4 py-8"><CrawlRequest initialReceipt={typeof receipt === "string" ? receipt : null} /></main>
}
