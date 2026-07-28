import { AtlasWebShell } from "atlas-web-shell"

import { apiUrl } from "@/lib/api"

export function SqlConsolePage() {
  return <AtlasWebShell apiBaseUrl={apiUrl("/")} />
}
