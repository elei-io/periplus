import { useMemo } from "react"

import { ExtractForm } from "@/components/extract-run/extract-form"
import { ExtractRunCell } from "@/components/extract-run/extract-run-cell"
import { PlaygroundPageHeader } from "@/components/playground-page-header"
import { PlaygroundRunStack } from "@/components/playground-run-stack"
import { useExtractRuns, useSubmitExtract } from "@/hooks/use-extract-runs"

const activeStatuses = new Set(["queued", "running"])
const recentStatuses = new Set(["succeeded", "failed"])

export function ExtractPage() {
  const runsQuery = useExtractRuns()
  const submitExtract = useSubmitExtract()
  const initialUrl = useMemo(() => {
    return new URLSearchParams(window.location.search).get("url") ?? ""
  }, [])
  const runs = runsQuery.data ?? []
  const activeRuns = runs.filter((run) => activeStatuses.has(run.status))
  const recentRuns = runs.filter((run) => recentStatuses.has(run.status))

  return (
    <div className="grid min-h-[calc(100svh-7rem)] w-full content-start py-9 md:py-11">
      <section className="mx-auto grid w-full max-w-4xl gap-6">
        <PlaygroundPageHeader
          title="Turn a page into data"
          description="Describe the records you want and watch Atlas turn a live page into structured results."
          taskNote="Every run is saved as a task"
          taskHref="/scheduled-work/tasks?primitive=extract"
        />
        <ExtractForm
          idPrefix="extract-playground"
          initialUrl={initialUrl}
          isSubmitting={submitExtract.isPending}
          onSubmit={(input) =>
            submitExtract.mutateAsync(input).then(() => undefined)
          }
        />
        <PlaygroundRunStack
          activeRuns={activeRuns}
          ariaLabel="Extract activity"
          recentRuns={recentRuns}
          renderRun={(run, density) => (
            <ExtractRunCell run={run} density={density} />
          )}
        />
        {runsQuery.isLoading ? (
          <p className="px-1 text-xs text-muted-foreground">
            Loading activity…
          </p>
        ) : null}
        {runsQuery.isError ? (
          <p className="px-1 text-sm text-destructive">
            {runsQuery.error.message}
          </p>
        ) : null}
      </section>
    </div>
  )
}
