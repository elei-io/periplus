import type { FormEvent } from "react"
import { useMemo, useState } from "react"
import { CogIcon, SearchIcon, XIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { SearchInput } from "@/types/search"

type SearchFormProps = {
  idPrefix: string
  isRunning: boolean
  onCancel: () => void
  onSubmit: (input: SearchInput) => void
}

export function SearchForm({
  idPrefix,
  isRunning,
  onCancel,
  onSubmit,
}: SearchFormProps) {
  const [query, setQuery] = useState("")
  const [maxResults, setMaxResults] = useState(10)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [queryError, setQueryError] = useState("")
  const queryInputId = `${idPrefix}-query`
  const maxResultsId = `${idPrefix}-max-results`

  const activeSettingsCount = useMemo(() => {
    return Number(maxResults !== 10)
  }, [maxResults])

  const reset = () => {
    setQuery("")
    setMaxResults(10)
    setSettingsOpen(false)
    setQueryError("")
  }

  const submitSearchRequest = () => {
    const normalizedQuery = query.trim()

    if (!normalizedQuery) {
      setQueryError("Enter a search query.")
      return
    }

    setQueryError("")
    onSubmit({
      query: normalizedQuery,
      max_results: maxResults,
    })
    reset()
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    submitSearchRequest()
  }

  return (
    <>
      <form onSubmit={handleSubmit}>
        <div className="mx-auto grid w-full max-w-4xl gap-3">
          <div className="playground-command-bar flex flex-col gap-2 rounded-3xl border bg-card/85 p-1.5 shadow-[0_18px_60px_rgb(0_0_0/0.18),0_1px_0_rgb(255_255_255/0.06)_inset] backdrop-blur-xl sm:flex-row sm:items-center sm:rounded-full">
            <div className="flex min-w-0 flex-1 items-center gap-2 px-4">
              <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
              <Input
                id={queryInputId}
                className="h-10 rounded-none border-0 bg-transparent px-0 text-base shadow-none focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
                placeholder="Search the web"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                disabled={isRunning}
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
                      disabled={isRunning}
                      onClick={() => setSettingsOpen(true)}
                    />
                  }
                >
                  <CogIcon />
                  <span className="sr-only">Search settings</span>
                  {activeSettingsCount > 0 ? (
                    <span className="absolute -top-1 -right-1 flex size-4 items-center justify-center rounded-full bg-primary text-[10px] font-medium text-primary-foreground">
                      {activeSettingsCount}
                    </span>
                  ) : null}
                </TooltipTrigger>
                <TooltipContent>Search settings</TooltipContent>
              </Tooltip>

              <Button
                className="h-10 flex-1 rounded-full px-5 sm:flex-none"
                type="button"
                disabled={isRunning}
                onClick={submitSearchRequest}
              >
                <SearchIcon />
                Search
              </Button>
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
          {queryError ? (
            <p className="px-4 text-xs text-destructive">{queryError}</p>
          ) : null}
        </div>
      </form>

      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Search settings</DialogTitle>
            <DialogDescription>
              Tune how many organic results Atlas should collect.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-2">
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor={maxResultsId}>Max results</Label>
              <Input
                id={maxResultsId}
                className="h-8 w-20"
                type="number"
                min={1}
                max={100}
                value={maxResults}
                onChange={(event) => setMaxResults(Number(event.target.value))}
                disabled={isRunning}
              />
            </div>
            <Slider
              min={1}
              max={100}
              step={1}
              value={[maxResults]}
              onValueChange={(nextValue) =>
                setMaxResults(
                  Array.isArray(nextValue) ? (nextValue[0] ?? 1) : nextValue
                )
              }
              disabled={isRunning}
            />
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
