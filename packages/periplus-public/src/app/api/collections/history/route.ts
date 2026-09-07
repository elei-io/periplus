import { proxyCollection } from "@/server/collection-proxy"
export async function GET(request: Request) { return proxyCollection(request, `/collections/history${new URL(request.url).search}`) }
