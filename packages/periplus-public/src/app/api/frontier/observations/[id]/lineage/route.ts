import { proxyCollection } from "@/server/collection-proxy"
export async function GET(request: Request, context: RouteContext<"/api/frontier/observations/[id]/lineage">) {
  const { id } = await context.params
  return proxyCollection(request, `/frontier/observations/${encodeURIComponent(id)}/lineage${new URL(request.url).search}`)
}
