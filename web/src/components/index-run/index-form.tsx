import type { FormEvent } from "react"
import { useMemo, useState } from "react"
import { CogIcon, DatabaseIcon, LockIcon, XIcon } from "lucide-react"

import { IndexSettingsDialog } from "@/components/index-run/index-settings-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { IndexFilterRow, IndexInput } from "@/types/index"

type IndexFormProps = {
  idPrefix: string
  initialUrl?: string
  isLocked: boolean
  isRunning: boolean
  onCancel: () => void
  onSubmit: (input: IndexInput) => void
}

function emptyFilterBuckets() {
  return {
    include_crawl: [] as string[],
    exclude_crawl: [] as string[],
    include_result: [] as string[],
    exclude_result: [] as string[],
  }
}

export function IndexForm({
  idPrefix,
  initialUrl = "",
  isLocked,
  isRunning,
  onCancel,
  onSubmit,
}: IndexFormProps) {
  const [url, setUrl] = useState(initialUrl)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [maxDepth, setMaxDepth] = useState(1)
  const [dedupe, setDedupe] = useState(false)
  const [filters, setFilters] = useState<IndexFilterRow[]>([])
  const [urlError, setUrlError] = useState("")
  const urlInputId = `${idPrefix}-url`
  const maxDepthId = `${idPrefix}-max-depth`
  const dedupeId = `${idPrefix}-dedupe`
  const controlsDisabled = isRunning || isLocked

  const activeSettingsCount = useMemo(() => {
    return (
      Number(maxDepth !== 1) +
      Number(dedupe) +
      filters.filter((filter) => filter.value.trim()).length
    )
  }, [dedupe, filters, maxDepth])

  const reset = () => {
    setUrl("")
    setMaxDepth(1)
    setDedupe(false)
    setFilters([])
    setUrlError("")
    setSettingsOpen(false)
  }

  const submitIndexRequest = () => {
    if (isLocked) {
      return
    }

    try {
      const parsedUrl = new URL(url)

      if (!["http:", "https:"].includes(parsedUrl.protocol)) {
        setUrlError("Enter an http or https URL.")
        return
      }
    } catch {
      setUrlError("Enter a valid URL.")
      return
    }

    const filterBuckets = filters.reduce((buckets, filter) => {
      const value = filter.value.trim()

      if (value) {
        buckets[filter.type].push(value)
      }

      return buckets
    }, emptyFilterBuckets())

    setUrlError("")
    onSubmit({
      url,
      max_depth: maxDepth,
      dedupe,
      ...filterBuckets,
    })
    reset()
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    submitIndexRequest()
  }

  return (
    <>
      <form onSubmit={handleSubmit}>
        <div className="mx-auto grid w-full max-w-4xl gap-3">
          <div className="playground-command-bar flex flex-col gap-2 rounded-3xl border bg-card/85 p-1.5 shadow-[0_18px_60px_rgb(0_0_0/0.18),0_1px_0_rgb(255_255_255/0.06)_inset] backdrop-blur-xl sm:flex-row sm:items-center sm:rounded-full">
            <div className="flex min-w-0 flex-1 items-center gap-2 px-4">
              <DatabaseIcon className="size-4 shrink-0 text-muted-foreground" />
              <Input
                id={urlInputId}
                className="h-10 rounded-none border-0 bg-transparent px-0 text-base shadow-none focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
                type="url"
                placeholder="Index a URL"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                disabled={controlsDisabled}
                autoFocus
                required
              />
            </div>
            <div className="flex items-center gap-1.5 sm:shrink-0">
              <Tooltip>
                <TooltipTrigger
                  render={
                    <Button
                      className="relative size-9 rounded-full border-transparent bg-transparent text-muted-foreground hover:bg-muted/60 hover:text-foreground [&_svg]:size-4"
                      type="button"
                      variant="ghost"
                      size="icon"
                      disabled={controlsDisabled}
                      onClick={() => setSettingsOpen(true)}
                    />
                  }
                >
                  <CogIcon />
                  <span className="sr-only">Index settings</span>
                  {activeSettingsCount > 0 ? (
                    <span className="absolute -top-1 -right-1 flex size-4 items-center justify-center rounded-full bg-primary text-[10px] font-medium text-primary-foreground">
                      {activeSettingsCount}
                    </span>
                  ) : null}
                </TooltipTrigger>
                <TooltipContent>Index settings</TooltipContent>
              </Tooltip>

              {isLocked ? (
                <Badge className="h-10 rounded-full px-3" variant="secondary">
                  <LockIcon />
                  Locked
                </Badge>
              ) : (
                <Button
                  className="h-10 flex-1 rounded-full px-5 sm:flex-none"
                  type="button"
                  disabled={isRunning}
                  onClick={submitIndexRequest}
                >
                  <DatabaseIcon />
                  Index
                </Button>
              )}
              {isRunning ? (
                <Button
                  className="h-10 rounded-full"
                  type="button"
                  variant="outline"
                  onClick={onCancel}
                >
                  <XIcon />
                  Cancel
                </Button>
              ) : null}
            </div>
          </div>
          {urlError ? (
            <p className="px-4 text-xs text-destructive">{urlError}</p>
          ) : null}
        </div>
      </form>

      <IndexSettingsDialog
        dedupe={dedupe}
        dedupeId={dedupeId}
        disabled={controlsDisabled}
        filters={filters}
        maxDepth={maxDepth}
        maxDepthId={maxDepthId}
        open={settingsOpen}
        onDedupeChange={setDedupe}
        onFiltersChange={setFilters}
        onMaxDepthChange={setMaxDepth}
        onOpenChange={setSettingsOpen}
      />
    </>
  )
}
