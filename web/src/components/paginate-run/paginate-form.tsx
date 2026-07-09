import type { FormEvent } from "react"
import { useMemo, useState } from "react"
import { CogIcon, RouteIcon, XIcon } from "lucide-react"

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import type { PaginateInput } from "@/types/paginate"

type PaginateFormProps = {
  idPrefix: string
  initialUrl?: string
  isRunning: boolean
  onCancel: () => void
  onSubmit: (input: PaginateInput) => void
}

export function PaginateForm({
  idPrefix,
  initialUrl = "",
  isRunning,
  onCancel,
  onSubmit,
}: PaginateFormProps) {
  const [url, setUrl] = useState(initialUrl)
  const [maxPages, setMaxPages] = useState(5)
  const [mode, setMode] = useState<PaginateInput["mode"]>("app")
  const [wait, setWait] = useState<PaginateInput["wait"]>("stable")
  const [reuseExisting, setReuseExisting] = useState(true)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [urlError, setUrlError] = useState("")
  const urlInputId = `${idPrefix}-url`
  const maxPagesId = `${idPrefix}-max-pages`
  const activeSettingsCount = useMemo(
    () => Number(maxPages !== 5) + Number(mode !== "app") + Number(wait !== "stable") + Number(!reuseExisting),
    [maxPages, mode, reuseExisting, wait]
  )

  const submit = () => {
    const normalizedUrl = url.trim()
    if (!normalizedUrl) {
      setUrlError("Enter a URL.")
      return
    }

    setUrlError("")
    onSubmit({
      url: normalizedUrl,
      max_pages: maxPages,
      mode,
      wait,
      reuse_existing: reuseExisting,
    })
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    submit()
  }

  return (
    <>
      <form onSubmit={handleSubmit}>
        <div className="mx-auto grid w-full max-w-4xl gap-3">
          <div className="playground-command-bar flex flex-col gap-2 rounded-3xl border bg-card/85 p-1.5 shadow-[0_18px_60px_rgb(0_0_0/0.18),0_1px_0_rgb(255_255_255/0.06)_inset] backdrop-blur-xl sm:flex-row sm:items-center sm:rounded-full">
            <div className="flex min-w-0 flex-1 items-center gap-2 px-4">
              <RouteIcon className="size-4 shrink-0 text-muted-foreground" />
              <Input
                id={urlInputId}
                className="h-10 rounded-none border-0 bg-transparent px-0 text-base shadow-none focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
                placeholder="https://example.com/search?q=..."
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                disabled={isRunning}
                autoFocus
                required
              />
            </div>
            <div className="flex items-center gap-1.5 sm:shrink-0">
              <Button
                className="relative size-9 rounded-full border-transparent bg-transparent text-muted-foreground hover:bg-muted/60 hover:text-foreground [&_svg]:size-4"
                type="button"
                variant="ghost"
                size="icon"
                disabled={isRunning}
                onClick={() => setSettingsOpen(true)}
              >
                <CogIcon />
                <span className="sr-only">Pagination settings</span>
                {activeSettingsCount > 0 ? (
                  <span className="absolute -top-1 -right-1 flex size-4 items-center justify-center rounded-full bg-primary text-[10px] font-medium text-primary-foreground">
                    {activeSettingsCount}
                  </span>
                ) : null}
              </Button>
              <Button
                className="h-10 flex-1 rounded-full px-5 sm:flex-none"
                type="button"
                disabled={isRunning}
                onClick={submit}
              >
                <RouteIcon />
                Paginate
              </Button>
              {isRunning ? (
                <Button className="h-10 rounded-full" type="button" variant="outline" onClick={onCancel}>
                  <XIcon />
                  Cancel
                </Button>
              ) : null}
            </div>
          </div>
          {urlError ? <p className="px-4 text-xs text-destructive">{urlError}</p> : null}
        </div>
      </form>

      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Pagination settings</DialogTitle>
            <DialogDescription>
              Tune how far Atlas should advance while learning or reusing a pagination schema.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <div className="flex items-center justify-between gap-3">
                <Label htmlFor={maxPagesId}>Max pages</Label>
                <Input
                  id={maxPagesId}
                  className="h-8 w-20"
                  type="number"
                  min={1}
                  max={25}
                  value={maxPages}
                  onChange={(event) => setMaxPages(Number(event.target.value))}
                  disabled={isRunning}
                />
              </div>
              <Slider
                min={1}
                max={25}
                step={1}
                value={[maxPages]}
                onValueChange={(nextValue) =>
                  setMaxPages(Array.isArray(nextValue) ? (nextValue[0] ?? 1) : nextValue)
                }
                disabled={isRunning}
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Select value={mode} onValueChange={(value) => setMode(value as PaginateInput["mode"])}>
                <SelectTrigger>
                  <span>{mode}</span>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="static">static</SelectItem>
                  <SelectItem value="dynamic">dynamic</SelectItem>
                  <SelectItem value="app">app</SelectItem>
                </SelectContent>
              </Select>
              <Select value={wait} onValueChange={(value) => setWait(value as PaginateInput["wait"])}>
                <SelectTrigger>
                  <span>{wait}</span>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">none</SelectItem>
                  <SelectItem value="stable">stable</SelectItem>
                  <SelectItem value="network">network</SelectItem>
                  <SelectItem value="fixed">fixed</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <label className="flex items-center justify-between gap-3 rounded-md border p-3 text-sm">
              <span>Reuse matching schema</span>
              <Switch checked={reuseExisting} onCheckedChange={setReuseExisting} />
            </label>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
