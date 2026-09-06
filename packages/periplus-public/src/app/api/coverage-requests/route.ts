import { proxyCoverageRequest } from "@/server/coverage-request-proxy"
export async function GET(request: Request) {
  return proxyCoverageRequest(request, `/coverage-requests${new URL(request.url).search}`)
}
export async function POST(request: Request) {
  return proxyCoverageRequest(request, "/coverage-requests")
}
