import { proxyCollection } from "@/server/collection-proxy"
export async function GET(request: Request, context: RouteContext<"/api/collections/[id]/items">) { const { id } = await context.params; return proxyCollection(request, `/collections/${encodeURIComponent(id)}/items${new URL(request.url).search}`) }
