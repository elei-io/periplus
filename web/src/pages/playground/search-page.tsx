import { PlaygroundPageHeader } from "@/components/playground-page-header"
import { SearchForm } from "@/components/search-run/search-form"
import { SearchRunStack } from "@/components/search-run/search-run-stack"
import { useSearchRuns, useSubmitSearch } from "@/hooks/use-search-runs"

const activeStatuses = new Set(["queued", "running"])
const recentStatuses = new Set(["succeeded", "failed"])

export function SearchPage() {
  const runsQuery = useSearchRuns()
  const submitSearch = useSubmitSearch()
  const runs = runsQuery.data ?? []
  const activeRuns = runs.filter((run) => activeStatuses.has(run.status))
  const recentRuns = runs.filter((run) => recentStatuses.has(run.status))

  return (
    <div className="grid min-h-[calc(100svh-7rem)] w-full content-start py-9 md:py-11">
      <section className="mx-auto grid w-full max-w-4xl gap-6">
        <PlaygroundPageHeader
          title="Try a web search"
          description="Choose a live search provider, run a query, and inspect what Atlas finds."
          taskNote="Every run is saved as a task"
          taskHref="/scheduled-work/tasks?primitive=search"
        />
        <SearchForm
          idPrefix="search-playground"
          isSubmitting={submitSearch.isPending}
          onSubmit={(input) =>
            submitSearch.mutateAsync(input).then(() => undefined)
          }
        />
        <SearchRunStack activeRuns={activeRuns} recentRuns={recentRuns} />
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
