import { useMemo } from "react"

import { IndexForm } from "@/components/index-run/index-form"
import { IndexRunCell } from "@/components/index-run/index-run-cell"
import { PlaygroundPageHeader } from "@/components/playground-page-header"
import { PlaygroundRunStack } from "@/components/playground-run-stack"
import { useIndexRuns, useSubmitIndex } from "@/hooks/use-index-runs"

const activeStatuses = new Set(["queued", "running"])
const recentStatuses = new Set(["succeeded", "failed"])

export function IndexPage() {
  const runsQuery = useIndexRuns()
  const submitIndex = useSubmitIndex()
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
          title="Discover a site"
          description="Start from a page and see which links Atlas can follow. Tune depth and filters when you need boundaries."
          taskNote="Every run is saved as a task"
          taskHref="/scheduled-work/tasks?primitive=index"
        />
        <IndexForm
          idPrefix="index-playground"
          initialUrl={initialUrl}
          isSubmitting={submitIndex.isPending}
          onSubmit={(input) =>
            submitIndex.mutateAsync(input).then(() => undefined)
          }
        />
        <PlaygroundRunStack
          activeRuns={activeRuns}
          ariaLabel="Index activity"
          recentRuns={recentRuns}
          renderRun={(run, density) => (
            <IndexRunCell run={run} density={density} />
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
