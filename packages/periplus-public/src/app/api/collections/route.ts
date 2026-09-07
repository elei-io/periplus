import { proxyCollection } from "@/server/collection-proxy"
export async function GET(request: Request) {
  return proxyCollection(request, `/collections${new URL(request.url).search}`)
}
export async function POST(request: Request) {
  return proxyCollection(request, "/collections")
}
