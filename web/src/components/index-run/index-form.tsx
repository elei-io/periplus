import { DatabaseIcon, Loader2Icon } from "lucide-react"
import type { FormEvent } from "react"
import { useRef, useState } from "react"

import { IndexOptionsPopover } from "@/components/index-run/index-options-popover"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { IndexFilterRow, IndexInput } from "@/types/index"

type IndexFormProps = {
  idPrefix: string
  initialUrl?: string
  isSubmitting: boolean
  onSubmit: (input: IndexInput) => Promise<void>
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
  isSubmitting,
  onSubmit,
}: IndexFormProps) {
  const [url, setUrl] = useState(initialUrl)
  const [maxDepth, setMaxDepth] = useState(1)
  const [dedupe, setDedupe] = useState(false)
  const [filters, setFilters] = useState<IndexFilterRow[]>([])
  const [urlError, setUrlError] = useState("")
  const urlInputRef = useRef<HTMLInputElement>(null)
  const urlInputId = `${idPrefix}-url`
  const urlErrorId = `${idPrefix}-url-error`
  const dedupeId = `${idPrefix}-dedupe`
  const controlsDisabled = isSubmitting

  const reset = () => {
    setUrl("")
    setUrlError("")
  }

  const submitIndexRequest = async () => {
    const normalizedUrl = url.trim()
    try {
      const parsedUrl = new URL(normalizedUrl)

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
    try {
      await onSubmit({
        url: normalizedUrl,
        max_depth: maxDepth,
        dedupe,
        ...filterBuckets,
      })
      reset()
      window.requestAnimationFrame(() => urlInputRef.current?.focus())
    } catch {
      urlInputRef.current?.focus()
    }
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void submitIndexRequest()
  }

  return (
    <form aria-label="Run an index" onSubmit={handleSubmit}>
      <div className="mx-auto grid w-full max-w-4xl gap-3">
        <div className="playground-command-bar flex flex-col gap-2 rounded-3xl border bg-card/85 p-1.5 shadow-[0_18px_60px_rgb(0_0_0/0.18),0_1px_0_rgb(255_255_255/0.06)_inset] backdrop-blur-xl transition-[border-color,box-shadow] focus-within:border-primary/35 focus-within:ring-2 focus-within:ring-primary/10 sm:flex-row sm:items-center sm:rounded-full">
          <div className="flex min-w-0 flex-1 items-center gap-2 px-4">
            <DatabaseIcon className="size-4 shrink-0 text-muted-foreground" />
            <Label className="sr-only" htmlFor={urlInputId}>
              Starting URL
            </Label>
            <Input
              ref={urlInputRef}
              id={urlInputId}
              className="h-10 rounded-none border-0 bg-transparent px-0 text-base shadow-none focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
              type="url"
              placeholder="Where should Atlas start?"
              value={url}
              onChange={(event) => {
                setUrl(event.target.value)
                if (urlError) setUrlError("")
              }}
              disabled={controlsDisabled}
              aria-invalid={urlError ? true : undefined}
              aria-describedby={urlError ? urlErrorId : undefined}
              autoFocus
              required
            />
          </div>
          <div className="flex items-center gap-1.5 sm:shrink-0">
            <IndexOptionsPopover
              dedupe={dedupe}
              dedupeId={dedupeId}
              disabled={controlsDisabled}
              filters={filters}
              maxDepth={maxDepth}
              onDedupeChange={setDedupe}
              onFiltersChange={setFilters}
              onMaxDepthChange={setMaxDepth}
            />

            <Button
              className="h-10 flex-1 rounded-full px-5 shadow-lg shadow-primary/15 sm:flex-none"
              type="submit"
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <Loader2Icon className="animate-spin motion-reduce:animate-none" />
              ) : (
                <DatabaseIcon />
              )}
              {isSubmitting ? "Starting…" : "Index"}
            </Button>
          </div>
        </div>
        {urlError ? (
          <p
            id={urlErrorId}
            className="px-4 text-xs text-destructive"
            role="alert"
          >
            {urlError}
          </p>
        ) : null}
      </div>
    </form>
  )
}
