import { timingSafeEqual } from "node:crypto";
import { prometheusMetrics } from "@/server/telemetry";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export function GET(request: Request) {
  const token = process.env.PERIPLUS_QUERY_API_TOKEN;
  const expected = Buffer.from(`Bearer ${token}`);
  const supplied = Buffer.from(request.headers.get("authorization") ?? "");
  if (
    !token ||
    supplied.length !== expected.length ||
    !timingSafeEqual(supplied, expected)
  )
    return new Response(null, { status: 401 });
  return new Response(prometheusMetrics(), {
    headers: {
      "Content-Type": "text/plain; version=0.0.4",
      "Cache-Control": "no-store",
    },
  });
}
