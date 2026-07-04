import { PlusIcon } from "lucide-react"

import {
  FieldLabel,
  FilterEditor,
  InfoTooltip,
  NumberSlider,
  SelectField,
} from "@/components/index-run/index-settings-fields"
import type { SelectOption } from "@/components/index-run/index-settings-fields"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Switch } from "@/components/ui/switch"
import type {
  CrawlMode,
  CrawlWait,
  IndexFilterRow,
} from "@/types/index"

type IndexSettingsDialogProps = {
  concurrency: number
  dedupe: boolean
  disabled: boolean
  filters: IndexFilterRow[]
  maxDepth: number
  maxDepthId: string
  concurrencyId: string
  dedupeId: string
  mode: CrawlMode
  open: boolean
  wait: CrawlWait
  onConcurrencyChange: (value: number) => void
  onDedupeChange: (value: boolean) => void
  onFiltersChange: (filters: IndexFilterRow[]) => void
  onMaxDepthChange: (value: number) => void
  onModeChange: (value: CrawlMode) => void
  onOpenChange: (open: boolean) => void
  onWaitChange: (value: CrawlWait) => void
}

const crawlModes: Array<SelectOption<CrawlMode>> = [
  { value: "static", label: "Static" },
  { value: "dynamic", label: "Dynamic" },
  { value: "app", label: "App" },
]

const waitStrategies: Array<SelectOption<CrawlWait>> = [
  { value: "none", label: "None" },
  { value: "stable", label: "Stable" },
  { value: "network", label: "Network" },
  { value: "fixed", label: "Fixed" },
]

const settingHints = {
  mode: "Controls how Atlas loads pages. Static is fastest, dynamic scrolls and waits for richer pages, and app is best for heavily client-rendered sites.",
  wait: "Controls when Atlas decides a page is ready to inspect. Stable is a good default for pages that hydrate slowly.",
  maxDepth:
    "How many internal-link hops Atlas follows from the starting URL. Depth 1 only indexes links found on the starting page.",
  concurrency:
    "How many pages Atlas crawls at once. Higher values can finish faster but put more load on the target site.",
  dedupe:
    "When enabled, each discovered URL appears once, keeping the shallowest occurrence.",
  filters:
    "Optional glob patterns for limiting what Atlas crawls or what appears in the final results.",
}

function createFilter(): IndexFilterRow {
  return {
    id: crypto.randomUUID(),
    type: "exclude_result",
    value: "",
  }
}

export function IndexSettingsDialog({
  concurrency,
  concurrencyId,
  dedupe,
  dedupeId,
  disabled,
  filters,
  maxDepth,
  maxDepthId,
  mode,
  open,
  wait,
  onConcurrencyChange,
  onDedupeChange,
  onFiltersChange,
  onMaxDepthChange,
  onModeChange,
  onOpenChange,
  onWaitChange,
}: IndexSettingsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[min(760px,calc(100svh-2rem))] overflow-hidden sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Index settings</DialogTitle>
          <DialogDescription>
            Tune how Atlas loads pages, follows links, and filters the final
            result set.
          </DialogDescription>
        </DialogHeader>

        <div className="grid max-h-[calc(100svh-10rem)] gap-5 overflow-auto pr-1">
          <div className="grid gap-4 md:grid-cols-2">
            <SelectField
              label="Mode"
              hint={settingHints.mode}
              value={mode}
              disabled={disabled}
              options={crawlModes}
              onChange={onModeChange}
            />
            <SelectField
              label="Wait mode"
              hint={settingHints.wait}
              value={wait}
              disabled={disabled}
              options={waitStrategies}
              onChange={onWaitChange}
            />
          </div>

          <div className="grid gap-5 md:grid-cols-2">
            <NumberSlider
              id={maxDepthId}
              label="Max depth"
              hint={settingHints.maxDepth}
              min={0}
              max={5}
              value={maxDepth}
              disabled={disabled}
              onChange={onMaxDepthChange}
            />
            <NumberSlider
              id={concurrencyId}
              label="Concurrency"
              hint={settingHints.concurrency}
              min={1}
              max={50}
              value={concurrency}
              disabled={disabled}
              onChange={onConcurrencyChange}
            />
          </div>

          <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
            <div className="grid gap-1">
              <FieldLabel htmlFor={dedupeId} hint={settingHints.dedupe}>
                Dedupe URLs
              </FieldLabel>
              <p className="text-xs text-muted-foreground">
                Keep the shallowest discovered instance of each URL.
              </p>
            </div>
            <Switch
              id={dedupeId}
              checked={dedupe}
              onCheckedChange={onDedupeChange}
              disabled={disabled}
            />
          </div>

          <div className="grid gap-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-medium">Filters</h2>
                  <InfoTooltip>{settingHints.filters}</InfoTooltip>
                </div>
                <p className="text-xs text-muted-foreground">
                  Add glob patterns only when the run needs boundaries.
                </p>
              </div>
              <Button
                type="button"
                variant="outline"
                onClick={() => onFiltersChange([...filters, createFilter()])}
                disabled={disabled}
              >
                <PlusIcon />
                Add filter
              </Button>
            </div>

            {filters.length === 0 ? (
              <div className="rounded-lg border border-dashed p-4 text-xs text-muted-foreground">
                No filters added.
              </div>
            ) : (
              <div className="grid gap-2">
                {filters.map((filter) => (
                  <FilterEditor
                    key={filter.id}
                    filter={filter}
                    disabled={disabled}
                    onChange={(nextFilter) =>
                      onFiltersChange(
                        filters.map((currentFilter) =>
                          currentFilter.id === nextFilter.id
                            ? nextFilter
                            : currentFilter
                        )
                      )
                    }
                    onRemove={() =>
                      onFiltersChange(
                        filters.filter(
                          (currentFilter) => currentFilter.id !== filter.id
                        )
                      )
                    }
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
