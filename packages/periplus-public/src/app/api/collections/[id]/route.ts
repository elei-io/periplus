import { proxyCollection } from "@/server/collection-proxy"
export async function GET(request: Request, context: RouteContext<"/api/collections/[id]">) {
  const { id } = await context.params
  return proxyCollection(request, `/collections/${encodeURIComponent(id)}`)
}
