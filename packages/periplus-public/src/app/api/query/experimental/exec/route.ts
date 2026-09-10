import { proxyQuery } from "@/server/query-proxy"

export async function POST(request: Request) {
  return proxyQuery(request, "/query/exec", "experimental")
}
