import type { FormEvent } from "react"
import { useState } from "react"
import { FileSearchIcon, XIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { CrawlInput } from "@/types/crawl"

type CrawlFormProps = {
  idPrefix: string
  initialUrl?: string
  isRunning: boolean
  onCancel: () => void
  onSubmit: (input: CrawlInput) => void
}

export function CrawlForm({
  idPrefix,
  initialUrl = "",
  isRunning,
  onCancel,
  onSubmit,
}: CrawlFormProps) {
  const [url, setUrl] = useState(initialUrl)
  const [error, setError] = useState("")
  const urlInputId = `${idPrefix}-url`

  const reset = () => {
    setUrl("")
    setError("")
  }

  const submitCrawlRequest = () => {
    const normalizedUrl = url.trim()

    try {
      const parsedUrl = new URL(normalizedUrl)

      if (!["http:", "https:"].includes(parsedUrl.protocol)) {
        setError("Enter an http or https URL.")
        return
      }
    } catch {
      setError("Enter a valid URL.")
      return
    }

    setError("")
    onSubmit({
      urls: [normalizedUrl],
    })
    reset()
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    submitCrawlRequest()
  }

  return (
    <form onSubmit={handleSubmit}>
      <div className="mx-auto grid w-full max-w-4xl gap-3">
        <div className="playground-command-bar flex flex-col gap-2 rounded-3xl border bg-card/85 p-1.5 shadow-[0_18px_60px_rgb(0_0_0/0.18),0_1px_0_rgb(255_255_255/0.06)_inset] backdrop-blur-xl sm:flex-row sm:items-center sm:rounded-full">
          <div className="flex min-w-0 flex-1 items-center gap-2 px-4">
            <FileSearchIcon className="size-4 shrink-0 text-muted-foreground" />
            <Input
              id={urlInputId}
              className="h-10 rounded-none border-0 bg-transparent px-0 text-base shadow-none focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
              type="url"
              placeholder="Crawl a URL"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              disabled={isRunning}
              autoFocus
              required
            />
          </div>
          <div className="flex items-center gap-1.5 sm:shrink-0">
            <Button
              className="h-10 flex-1 rounded-full px-5 sm:flex-none"
              type="button"
              disabled={isRunning}
              onClick={submitCrawlRequest}
            >
              <FileSearchIcon />
              Crawl
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
        {error ? (
          <p className="px-4 text-xs text-destructive">{error}</p>
        ) : null}
      </div>
    </form>
  )
}
