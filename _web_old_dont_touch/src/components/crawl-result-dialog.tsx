import { Globe2Icon, LoaderCircleIcon, PlayIcon } from "lucide-react"
import { useId, useMemo, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { useCrawlGraphs, useTriggerCrawlGraph } from "@/hooks/use-crawl-graphs"

const MAX_CRAWLS = 1_000_000
const MAX_START_URLS = 10_000

type UrlSelection = {
  urls: string[]
  invalidCount: number
  duplicateCount: number
}

export type CrawlResultRun = {
  runId: string
  planSlug: string
  startUrlCount: number
}

export function CrawlResultDialog({
  columns,
  rows,
  onStarted,
}: {
  columns: string[]
  rows: unknown[][]
  onStarted?: (run: CrawlResultRun) => void
}) {
  const plansQuery = useCrawlGraphs()
  const plans = useMemo(
    () => (plansQuery.data?.items ?? []).filter((plan) => plan.root_node_id),
    [plansQuery.data?.items]
  )
  const fieldId = useId()
  const [open, setOpen] = useState(false)
  const [urlColumnIndex, setUrlColumnIndex] = useState(() =>
    suggestUrlColumnIndex(columns, rows)
  )
  const [planId, setPlanId] = useState("")
  const [maxCrawls, setMaxCrawls] = useState("1000")
  const [maxRunDays, setMaxRunDays] = useState("7")
  const suggestedUrlColumnIndex = useMemo(
    () => suggestUrlColumnIndex(columns, rows),
    [columns, rows]
  )
  const selectedUrlColumnIndex =
    urlColumnIndex >= 0 && urlColumnIndex < columns.length
      ? urlColumnIndex
      : suggestedUrlColumnIndex
  const selectedPlanId = plans.some((plan) => plan.id === planId)
    ? planId
    : (plans[0]?.id ?? "")
  const trigger = useTriggerCrawlGraph(selectedPlanId)
  const selection = useMemo(
    () => collectUrls(rows, selectedUrlColumnIndex),
    [rows, selectedUrlColumnIndex]
  )

  const startCrawl = () => {
    if (!selectedPlanId) {
      toast.error("Choose a crawl plan.")
      return
    }
    if (selection.urls.length === 0) {
      toast.error("The selected column has no absolute HTTP(S) URLs.")
      return
    }
    if (selection.urls.length > MAX_START_URLS) {
      toast.error(
        `A crawl can start from at most ${MAX_START_URLS.toLocaleString()} URLs.`
      )
      return
    }
    const crawlBudget = Number(maxCrawls)
    if (
      !Number.isInteger(crawlBudget) ||
      crawlBudget < selection.urls.length ||
      crawlBudget > MAX_CRAWLS
    ) {
      toast.error(
        `Maximum pages must be between ${selection.urls.length.toLocaleString()} and ${MAX_CRAWLS.toLocaleString()}.`
      )
      return
    }
    const runDays = Number(maxRunDays)
    if (!Number.isInteger(runDays) || runDays < 1 || runDays > 365) {
      toast.error("Maximum run duration must be between 1 and 365 days.")
      return
    }

    trigger.mutate(
      {
        urls: selection.urls,
        max_crawls: crawlBudget,
        max_run_seconds: runDays * 24 * 60 * 60,
      },
      {
        onSuccess: (submission) => {
          const planSlug =
            plans.find((plan) => plan.id === selectedPlanId)?.slug ??
            selectedPlanId
          toast.success(
            `Crawl run ${submission.run_id} queued with ${selection.urls.length.toLocaleString()} start URLs.`
          )
          onStarted?.({
            runId: submission.run_id,
            planSlug,
            startUrlCount: selection.urls.length,
          })
          setOpen(false)
        },
      }
    )
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button size="sm" variant="outline" />}>
        <Globe2Icon data-icon="inline-start" />
        Crawl
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Crawl URLs from this result</DialogTitle>
          <DialogDescription>
            Send the unique URLs in one result column to a crawl plan as a
            single run.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor={`${fieldId}-url-column`}>URL column</Label>
            <Select
              value={
                selectedUrlColumnIndex >= 0
                  ? String(selectedUrlColumnIndex)
                  : null
              }
              onValueChange={(value) =>
                value && setUrlColumnIndex(Number(value))
              }
            >
              <SelectTrigger
                id={`${fieldId}-url-column`}
                className="w-full font-mono"
              >
                <span>
                  {columns[selectedUrlColumnIndex] || "Select column"}
                </span>
              </SelectTrigger>
              <SelectContent>
                {columns.map((column, index) => (
                  <SelectItem key={`${column}-${index}`} value={String(index)}>
                    {column}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <UrlSelectionSummary selection={selection} />
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor={`${fieldId}-plan`}>Crawl plan</Label>
            <Select
              value={selectedPlanId || null}
              onValueChange={(value) => value && setPlanId(value)}
            >
              <SelectTrigger id={`${fieldId}-plan`} className="w-full">
                <span>
                  {plans.find((plan) => plan.id === selectedPlanId)?.slug ??
                    (plansQuery.isLoading ? "Loading plans…" : "Select plan")}
                </span>
              </SelectTrigger>
              <SelectContent>
                {plans.map((plan) => (
                  <SelectItem key={plan.id} value={plan.id}>
                    {plan.slug}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {!plansQuery.isLoading && plans.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                Create a crawl plan with a root node first.
              </p>
            ) : null}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor={`${fieldId}-max-pages`}>Maximum pages</Label>
              <Input
                id={`${fieldId}-max-pages`}
                type="number"
                min={Math.max(1, selection.urls.length)}
                max={MAX_CRAWLS}
                value={maxCrawls}
                onChange={(event) => setMaxCrawls(event.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={`${fieldId}-max-days`}>Maximum days</Label>
              <Input
                id={`${fieldId}-max-days`}
                type="number"
                min={1}
                max={365}
                value={maxRunDays}
                onChange={(event) => setMaxRunDays(event.target.value)}
              />
            </div>
          </div>
        </div>

        <DialogFooter showCloseButton>
          <Button
            disabled={
              trigger.isPending ||
              !selectedPlanId ||
              selection.urls.length === 0 ||
              selection.urls.length > MAX_START_URLS ||
              plans.length === 0
            }
            onClick={startCrawl}
          >
            {trigger.isPending ? (
              <LoaderCircleIcon className="animate-spin" />
            ) : (
              <PlayIcon />
            )}
            Start crawl
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function UrlSelectionSummary({ selection }: { selection: UrlSelection }) {
  return (
    <p className="text-xs text-muted-foreground">
      {selection.urls.length.toLocaleString()} unique HTTP(S){" "}
      {selection.urls.length === 1 ? "URL" : "URLs"}
      {selection.invalidCount > 0
        ? ` · ${selection.invalidCount.toLocaleString()} ignored`
        : ""}
      {selection.duplicateCount > 0
        ? ` · ${selection.duplicateCount.toLocaleString()} duplicates removed`
        : ""}
    </p>
  )
}

function suggestUrlColumnIndex(columns: string[], rows: unknown[][]): number {
  if (columns.length === 0) {
    return -1
  }
  const scores = columns.map((column, index) => ({
    column,
    index,
    count: collectUrls(rows, index).urls.length,
  }))
  const preferred = ["target_url", "url", "resolved_url", "source_url"]
  for (const name of preferred) {
    const match = scores.find(
      ({ column, count }) => column.toLowerCase() === name && count > 0
    )
    if (match) {
      return match.index
    }
  }
  const urlNamed = scores
    .filter(({ column, count }) => /url/i.test(column) && count > 0)
    .sort((left, right) => right.count - left.count || left.index - right.index)
  if (urlNamed[0]) {
    return urlNamed[0].index
  }
  return scores.sort(
    (left, right) => right.count - left.count || left.index - right.index
  )[0].index
}

function collectUrls(rows: unknown[][], columnIndex: number): UrlSelection {
  if (columnIndex < 0) {
    return { urls: [], invalidCount: rows.length, duplicateCount: 0 }
  }

  const urls: string[] = []
  const seen = new Set<string>()
  let invalidCount = 0
  let duplicateCount = 0
  for (const row of rows) {
    const value = row[columnIndex]
    if (typeof value !== "string" || value.trim() === "") {
      invalidCount += 1
      continue
    }
    let normalized: string
    try {
      const parsed = new URL(value.trim())
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        invalidCount += 1
        continue
      }
      normalized = parsed.href
    } catch {
      invalidCount += 1
      continue
    }
    if (seen.has(normalized)) {
      duplicateCount += 1
      continue
    }
    seen.add(normalized)
    urls.push(normalized)
  }
  return { urls, invalidCount, duplicateCount }
}
