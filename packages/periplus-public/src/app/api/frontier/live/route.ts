import { proxyCollection } from "@/server/collection-proxy"
export async function GET(request: Request) {
  const collection = new URL(request.url).searchParams.get("collection_id")
  return proxyCollection(request, `/frontier/live${collection ? `?collection_id=${encodeURIComponent(collection)}` : ""}`)
}
