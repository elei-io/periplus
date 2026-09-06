import { proxyCoverageRequest } from "@/server/coverage-request-proxy"
export async function GET(request: Request, context: RouteContext<"/api/coverage-requests/[id]">) {
  const { id } = await context.params
  return proxyCoverageRequest(request, `/coverage-requests/${encodeURIComponent(id)}`)
}
