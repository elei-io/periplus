import { proxyQuery } from "@/server/query-proxy"

export async function GET(request: Request) {
  return proxyQuery(request, "/query/helpers", "experimental")
}
