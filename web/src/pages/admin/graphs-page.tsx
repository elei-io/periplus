import { GitForkIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"

export function GraphsPage() {
  return (
    <div className="flex flex-1 items-center justify-center px-6 py-16">
      <div className="flex max-w-lg flex-col items-center gap-5 text-center">
        <div className="flex size-14 items-center justify-center rounded-2xl border bg-card shadow-sm">
          <GitForkIcon className="size-7 text-muted-foreground" />
        </div>
        <div className="space-y-2">
          <div className="flex items-center justify-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">Crawl Graphs</h1>
            <Badge variant="secondary">Coming soon</Badge>
          </div>
          <p className="text-sm leading-6 text-muted-foreground">
            Graph nodes will map URL inputs to crawl work, while SQL edges derive
            the URLs passed to subsequent nodes.
          </p>
        </div>
      </div>
    </div>
  )
}
