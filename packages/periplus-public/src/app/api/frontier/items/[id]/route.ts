import { proxyCollection } from "@/server/collection-proxy"
export async function GET(request: Request, context: RouteContext<"/api/frontier/items/[id]">) { const { id } = await context.params; return proxyCollection(request, `/frontier/items/${encodeURIComponent(id)}${new URL(request.url).search}`) }
