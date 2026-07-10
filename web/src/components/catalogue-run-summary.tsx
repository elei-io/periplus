import { DatabaseIcon } from "lucide-react"

import type { TaskResultSummary } from "@/types/tasks"

type CatalogueRunSummaryProps = {
  result: TaskResultSummary
}

export function CatalogueRunSummary({ result }: CatalogueRunSummaryProps) {
  return (
    <div className="grid gap-3 text-sm">
      <div className="flex items-center gap-2 font-medium">
        <DatabaseIcon className="size-4 text-muted-foreground" />
        {result.catalogue ? "Stored in the catalogue" : "No catalogue usage"}
      </div>
      <dl className="flex flex-wrap gap-x-6 gap-y-2">
        {Object.entries(result.counts).map(([name, value]) => (
          <div key={name}>
            <dt className="text-xs text-muted-foreground capitalize">
              {name.replaceAll("_", " ")}
            </dt>
            <dd className="font-medium tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      {result.catalogue ? (
        <p className="text-xs break-all text-muted-foreground">
          Catalogue run {result.catalogue.run_id}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">
          This run did not use catalogue crawl data.
        </p>
      )}
    </div>
  )
}
