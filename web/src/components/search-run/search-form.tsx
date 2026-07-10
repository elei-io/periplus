import { Loader2Icon, SearchIcon } from "lucide-react"
import type { FormEvent } from "react"
import { useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { searchProviders } from "@/types/search"
import type { SearchInput, SearchProvider } from "@/types/search"

type SearchFormProps = {
  idPrefix: string
  isSubmitting: boolean
  onSubmit: (input: SearchInput) => Promise<void>
}

export function SearchForm({
  idPrefix,
  isSubmitting,
  onSubmit,
}: SearchFormProps) {
  const [query, setQuery] = useState("")
  const [maxPages, setMaxPages] = useState(1)
  const [provider, setProvider] = useState<SearchProvider>("duckduckgo")
  const [queryError, setQueryError] = useState("")
  const queryInputRef = useRef<HTMLInputElement>(null)
  const queryInputId = `${idPrefix}-query`
  const queryErrorId = `${idPrefix}-query-error`
  const maxPagesId = `${idPrefix}-max-pages`
  const providerInputId = `${idPrefix}-provider`
  const selectedProviderLabel =
    searchProviders.find((option) => option.value === provider)?.label ??
    provider

  const reset = () => {
    setQuery("")
    setQueryError("")
  }

  const submitSearchRequest = async () => {
    const normalizedQuery = query.trim()

    if (!normalizedQuery) {
      setQueryError("Enter a search query.")
      return
    }

    setQueryError("")
    try {
      await onSubmit({
        query: normalizedQuery,
        max_pages: maxPages,
        provider,
      })
      reset()
      window.requestAnimationFrame(() => queryInputRef.current?.focus())
    } catch {
      queryInputRef.current?.focus()
    }
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void submitSearchRequest()
  }

  return (
    <form aria-label="Run a web search" onSubmit={handleSubmit}>
      <div className="mx-auto grid w-full max-w-4xl gap-3">
        <div className="playground-command-bar flex flex-col gap-2 rounded-3xl border bg-card/85 p-1.5 shadow-[0_18px_60px_rgb(0_0_0/0.18),0_1px_0_rgb(255_255_255/0.06)_inset] backdrop-blur-xl transition-[border-color,box-shadow] focus-within:border-primary/35 focus-within:ring-2 focus-within:ring-primary/10 lg:flex-row lg:items-center lg:rounded-full">
          <div className="px-2 lg:w-36 lg:px-0 lg:pl-2">
            <Label className="sr-only" htmlFor={providerInputId}>
              Search provider
            </Label>
            <Select
              value={provider}
              onValueChange={(value) => {
                if (value !== null) {
                  setProvider(value as SearchProvider)
                }
              }}
              disabled={isSubmitting}
            >
              <SelectTrigger
                id={providerInputId}
                className="h-10 w-full rounded-full border-transparent bg-muted/50"
              >
                <span>{selectedProviderLabel}</span>
              </SelectTrigger>
              <SelectContent>
                {searchProviders.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex min-w-0 flex-1 items-center gap-2 px-4">
            <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
            <Label className="sr-only" htmlFor={queryInputId}>
              Search query
            </Label>
            <Input
              ref={queryInputRef}
              id={queryInputId}
              className="h-10 rounded-none border-0 bg-transparent px-0 text-base shadow-none focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
              placeholder="What do you want to find?"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value)
                if (queryError) setQueryError("")
              }}
              disabled={isSubmitting}
              aria-invalid={queryError ? true : undefined}
              aria-describedby={queryError ? queryErrorId : undefined}
              autoFocus
              required
            />
          </div>
          <div className="grid gap-1 px-2 sm:grid-cols-[minmax(7rem,8rem)_auto_auto] sm:items-center sm:px-0 lg:shrink-0">
            <div className="flex h-10 items-center gap-2 rounded-full bg-muted/50 px-3">
              <Label
                className="shrink-0 text-xs font-medium text-muted-foreground"
                htmlFor={maxPagesId}
              >
                Pages
              </Label>
              <Input
                id={maxPagesId}
                className="h-8 w-12 rounded-none border-0 bg-transparent p-0 text-center shadow-none focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
                type="number"
                min={1}
                max={25}
                value={maxPages}
                onChange={(event) =>
                  setMaxPages(
                    Math.min(25, Math.max(1, Number(event.target.value) || 1))
                  )
                }
                disabled={isSubmitting}
              />
            </div>

            <Button
              className="h-10 flex-1 rounded-full px-5 shadow-lg shadow-primary/15 sm:flex-none"
              type="submit"
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <Loader2Icon className="animate-spin motion-reduce:animate-none" />
              ) : (
                <SearchIcon />
              )}
              {isSubmitting ? "Starting…" : "Search"}
            </Button>
          </div>
        </div>
        {queryError ? (
          <p
            id={queryErrorId}
            className="px-4 text-xs text-destructive"
            role="alert"
          >
            {queryError}
          </p>
        ) : null}
      </div>
    </form>
  )
}
