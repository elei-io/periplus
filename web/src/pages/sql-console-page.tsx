import { AtlasWebShell } from "atlas-web-shell"

import { apiUrl } from "@/lib/api"

export function SqlConsolePage() {
  const initialSql =
    new URLSearchParams(window.location.search).get("sql") ?? ""
  return <AtlasWebShell apiBaseUrl={apiUrl("/")} initialSql={initialSql} />
}
