import { proxyCollection } from "@/server/collection-proxy"
export async function GET(request: Request) {
  const cursor = new URL(request.url).searchParams.get("cursor")
  return proxyCollection(request, `/frontier/captures${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`)
}
