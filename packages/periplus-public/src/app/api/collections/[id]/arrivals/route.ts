import { proxyCollection } from "@/server/collection-proxy"
export async function GET(request: Request, context: RouteContext<"/api/collections/[id]/arrivals">) { const { id } = await context.params; return proxyCollection(request, `/collections/${encodeURIComponent(id)}/arrivals${new URL(request.url).search}`) }
