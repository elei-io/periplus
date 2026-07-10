import type { FormEvent } from "react"
import { useState } from "react"
import { SparklesIcon, XIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import type { ExtractInput } from "@/types/extract"

type ExtractFormProps = {
  idPrefix: string
  initialUrl?: string
  isRunning: boolean
  onCancel: () => void
  onSubmit: (input: ExtractInput) => void
}

export function ExtractForm({
  idPrefix,
  initialUrl = "",
  isRunning,
  onCancel,
  onSubmit,
}: ExtractFormProps) {
  const [url, setUrl] = useState(initialUrl)
  const [prompt, setPrompt] = useState("")
  const [extractData, setExtractData] = useState(true)
  const [extractQueryParams, setExtractQueryParams] = useState(true)
  const [error, setError] = useState("")
  const urlInputId = `${idPrefix}-url`
  const promptInputId = `${idPrefix}-prompt`

  const reset = () => {
    setUrl("")
    setPrompt("")
    setError("")
  }

  const submitExtractRequest = () => {
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

    if (!extractData && !extractQueryParams) {
      setError("Enable at least one extraction mode.")
      return
    }

    if (extractData && !normalizedPrompt) {
      setError("Describe what Atlas should extract.")
      return
    }

    setError("")
    onSubmit({
      url: normalizedUrl,
      extract_data: extractData,
      extract_query_params: extractQueryParams,
      prompt: normalizedPrompt || null,
      target_json_example: null,
      schema_type: "css",
    })
    reset()
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    submitExtractRequest()
  }

  return (
    <form onSubmit={handleSubmit}>
      <div className="mx-auto grid w-full max-w-4xl gap-3">
        <div className="playground-command-bar grid gap-2 rounded-3xl border bg-card/85 p-1.5 shadow-[0_18px_60px_rgb(0_0_0/0.18),0_1px_0_rgb(255_255_255/0.06)_inset] backdrop-blur-xl">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <div className="flex min-w-0 flex-1 items-center gap-2 px-4">
              <SparklesIcon className="size-4 shrink-0 text-muted-foreground" />
              <Input
                id={urlInputId}
                className="h-10 rounded-none border-0 bg-transparent px-0 text-base shadow-none focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
                type="url"
                placeholder="Extract from a URL"
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
                onClick={submitExtractRequest}
              >
                <SparklesIcon />
                Extract
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

          <div className="flex flex-wrap items-center gap-2 px-3 pb-1">
            <ExtractionModeToggle
              label="Data"
              checked={extractData}
              disabled={isRunning}
              onChange={setExtractData}
            />
            <ExtractionModeToggle
              label="Query params"
              checked={extractQueryParams}
              disabled={isRunning}
              onChange={setExtractQueryParams}
            />
          </div>

          {extractData ? (
            <Textarea
              id={promptInputId}
              className="min-h-24 resize-y rounded-2xl border-0 bg-muted/55 px-4 py-3 text-sm shadow-none focus-visible:border-0 focus-visible:ring-1 dark:bg-muted/35"
              placeholder="Describe the structured data you want, e.g. Extract product cards with title, price, rating, and product URL."
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              disabled={isRunning}
              required
            />
          ) : null}
        </div>
        {error ? (
          <p className="px-4 text-xs text-destructive">{error}</p>
        ) : null}
      </div>
    </form>
  )
}

function ExtractionModeToggle({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string
  checked: boolean
  disabled: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <label className="flex h-8 items-center gap-2 rounded-full border bg-background/70 px-3 text-sm text-foreground shadow-sm">
      <Switch checked={checked} disabled={disabled} onCheckedChange={onChange} />
      <span>{label}</span>
    </label>
  )
}
