import { PeriplusWebShell } from "periplus-web-shell"

import { apiUrl } from "@/lib/api"

export function ConsolePage() {
  const initialSql =
    new URLSearchParams(window.location.search).get("sql") ?? ""
  return <PeriplusWebShell access="admin" historyKey="periplus.admin.sql.history" apiBaseUrl={apiUrl("/")} initialSql={initialSql} />
}
