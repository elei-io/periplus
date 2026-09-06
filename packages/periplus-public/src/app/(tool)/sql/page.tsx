import type { Metadata } from "next"

import { SqlTerminal } from "@/components/site/sql-terminal"

export const metadata: Metadata = {
  title: "SQL terminal",
  description: "Run bounded read-only SQL against the public Periplus catalogue.",
}

export default function SqlPage() {
  return (
    <main className="h-full min-h-0" aria-label="Periplus SQL terminal">
      <SqlTerminal />
    </main>
  )
}
