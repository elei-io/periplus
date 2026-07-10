import { Loader2Icon, SparklesIcon } from "lucide-react"
import type { FormEvent } from "react"
import { useRef, useState } from "react"

import { ExtractOptionsPopover } from "@/components/extract-run/extract-options-popover"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import type { ExtractInput } from "@/types/extract"

type ExtractFormProps = {
  idPrefix: string
  initialUrl?: string
  isSubmitting: boolean
  onSubmit: (input: ExtractInput) => Promise<void>
}

export function ExtractForm({
  idPrefix,
  initialUrl = "",
  isSubmitting,
  onSubmit,
}: ExtractFormProps) {
  const [url, setUrl] = useState(initialUrl)
  const [prompt, setPrompt] = useState("")
  const [extractData, setExtractData] = useState(true)
  const [extractQueryParams, setExtractQueryParams] = useState(true)
  const [error, setError] = useState("")
  const urlInputRef = useRef<HTMLInputElement>(null)
  const urlInputId = `${idPrefix}-url`
  const promptInputId = `${idPrefix}-prompt`
  const errorId = `${idPrefix}-error`

  const reset = () => {
    setUrl("")
    setPrompt("")
    setError("")
  }

  const submitExtractRequest = async () => {
    const normalizedUrl = url.trim()
    const normalizedPrompt = prompt.trim()

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

    if (extractData && !normalizedPrompt) {
      setError("Describe the records Atlas should extract.")
      return
    }

    setError("")
    try {
      await onSubmit({
        url: normalizedUrl,
        extract_data: extractData,
        extract_query_params: extractQueryParams,
        prompt: normalizedPrompt || null,
        target_json_example: null,
        schema_type: "css",
      })
      reset()
      window.requestAnimationFrame(() => urlInputRef.current?.focus())
    } catch {
      urlInputRef.current?.focus()
    }
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void submitExtractRequest()
  }

  return (
    <form aria-label="Run an extraction" onSubmit={handleSubmit}>
      <div className="mx-auto grid w-full max-w-4xl gap-3">
        <div className="playground-command-bar grid gap-1.5 rounded-3xl border bg-card/85 p-1.5 shadow-[0_18px_60px_rgb(0_0_0/0.18),0_1px_0_rgb(255_255_255/0.06)_inset] backdrop-blur-xl transition-[border-color,box-shadow] focus-within:border-primary/35 focus-within:ring-2 focus-within:ring-primary/10">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <div className="flex min-w-0 flex-1 items-center gap-2 px-4">
              <SparklesIcon className="size-4 shrink-0 text-muted-foreground" />
              <Label className="sr-only" htmlFor={urlInputId}>
                Page URL
              </Label>
              <Input
                ref={urlInputRef}
                id={urlInputId}
                className="h-10 rounded-none border-0 bg-transparent px-0 text-base shadow-none focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
                type="url"
                placeholder="Which page should Atlas extract?"
                value={url}
                onChange={(event) => {
                  setUrl(event.target.value)
                  if (error) setError("")
                }}
                disabled={isSubmitting}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? errorId : undefined}
                autoFocus
                required
              />
            </div>

            <div className="flex items-center gap-1.5 sm:shrink-0">
              <ExtractOptionsPopover
                disabled={isSubmitting}
                extractData={extractData}
                extractQueryParams={extractQueryParams}
                onExtractDataChange={setExtractData}
                onExtractQueryParamsChange={setExtractQueryParams}
              />
              <Button
                className="h-10 flex-1 rounded-full px-5 shadow-lg shadow-primary/15 sm:flex-none"
                type="submit"
                disabled={isSubmitting}
              >
                {isSubmitting ? (
                  <Loader2Icon className="animate-spin motion-reduce:animate-none" />
                ) : (
                  <SparklesIcon />
                )}
                {isSubmitting ? "Starting…" : "Extract"}
              </Button>
            </div>
          </div>

          {extractData ? (
            <div className="relative">
              <Label className="sr-only" htmlFor={promptInputId}>
                Extraction instructions
              </Label>
              <Textarea
                id={promptInputId}
                className="min-h-20 resize-y rounded-[1.15rem] border-0 bg-muted/45 px-4 py-3 text-sm leading-5 shadow-none focus-visible:border-0 focus-visible:ring-1 dark:bg-muted/30"
                placeholder="Describe the records you want — for example, product name, price, rating, and URL."
                value={prompt}
                onChange={(event) => {
                  setPrompt(event.target.value)
                  if (error) setError("")
                }}
                disabled={isSubmitting}
                required
              />
            </div>
          ) : (
            <p className="px-4 py-2 text-xs text-muted-foreground">
              Atlas will discover the page&apos;s query and pagination controls.
            </p>
          )}
        </div>

        {error ? (
          <p
            id={errorId}
            className="px-4 text-xs text-destructive"
            role="alert"
          >
            {error}
          </p>
        ) : null}
      </div>
    </form>
  )
}
