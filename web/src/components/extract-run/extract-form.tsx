import type { FormEvent } from "react"
import { useMemo, useState } from "react"
import { CogIcon, SparklesIcon, XIcon } from "lucide-react"

import { ExtractSettingsDialog } from "@/components/extract-run/extract-settings-dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { ExtractInput, DataSchemaType } from "@/types/extract"
import type { CrawlMode, CrawlWait } from "@/types/index"

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
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [schemaType, setSchemaType] = useState<DataSchemaType>("css")
  const [mode, setMode] = useState<CrawlMode>("static")
  const [wait, setWait] = useState<CrawlWait>("none")
  const [targetJsonExample, setTargetJsonExample] = useState("")
  const [error, setError] = useState("")
  const urlInputId = `${idPrefix}-url`
  const promptInputId = `${idPrefix}-prompt`
  const targetJsonExampleId = `${idPrefix}-target-json-example`

  const activeSettingsCount = useMemo(() => {
    return (
      Number(schemaType !== "css") +
      Number(mode !== "static") +
      Number(wait !== "none") +
      Number(extractData && Boolean(targetJsonExample.trim()))
    )
  }, [extractData, mode, schemaType, targetJsonExample, wait])

  const reset = () => {
    setUrl("")
    setPrompt("")
    setSchemaType("css")
    setMode("static")
    setWait("none")
    setTargetJsonExample("")
    setSettingsOpen(false)
    setError("")
  }

  const submitExtractRequest = () => {
    const normalizedUrl = url.trim()
    const normalizedPrompt = prompt.trim()
    const normalizedTargetJsonExample = targetJsonExample.trim()

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

    if (extractData && normalizedTargetJsonExample) {
      try {
        JSON.parse(normalizedTargetJsonExample)
      } catch {
        setError("Target JSON example must be valid JSON.")
        return
      }
    }

    setError("")
    onSubmit({
      url: normalizedUrl,
      extract_data: extractData,
      extract_query_params: extractQueryParams,
      prompt: normalizedPrompt || null,
      target_json_example: extractData ? normalizedTargetJsonExample || null : null,
      schema_type: schemaType,
      mode,
      wait,
    })
    reset()
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    submitExtractRequest()
  }

  return (
    <>
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
                    <span className="sr-only">Extract settings</span>
                    {activeSettingsCount > 0 ? (
                      <span className="absolute -top-1 -right-1 flex size-4 items-center justify-center rounded-full bg-primary text-[10px] font-medium text-primary-foreground">
                        {activeSettingsCount}
                      </span>
                    ) : null}
                  </TooltipTrigger>
                  <TooltipContent>Extract settings</TooltipContent>
                </Tooltip>

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

      <ExtractSettingsDialog
        dataEnabled={extractData}
        disabled={isRunning}
        mode={mode}
        open={settingsOpen}
        schemaType={schemaType}
        targetJsonExample={targetJsonExample}
        targetJsonExampleId={targetJsonExampleId}
        wait={wait}
        onModeChange={setMode}
        onOpenChange={setSettingsOpen}
        onSchemaTypeChange={setSchemaType}
        onTargetJsonExampleChange={setTargetJsonExample}
        onWaitChange={setWait}
      />
    </>
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
