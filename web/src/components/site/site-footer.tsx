import Link from "next/link"

import { Separator } from "@/components/ui/separator"

export function SiteFooter() {
  return (
    <footer className="mt-auto">
      <Separator />
      <div className="mx-auto flex max-w-7xl flex-col gap-3 px-4 py-6 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-6 lg:px-8">
        <p>Periplus turns web observations into durable SQL evidence.</p>
        <nav className="flex items-center gap-4" aria-label="Footer">
          <Link className="hover:text-foreground" href="/docs">
            Documentation
          </Link>
          <Link className="hover:text-foreground" href="/sql">
            SQL terminal
          </Link>
        </nav>
      </div>
    </footer>
  )
}
