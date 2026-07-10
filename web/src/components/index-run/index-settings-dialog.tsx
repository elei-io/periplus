import { PlusIcon } from "lucide-react"

import {
  FieldLabel,
  FilterEditor,
  InfoTooltip,
  NumberSlider,
} from "@/components/index-run/index-settings-fields"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Switch } from "@/components/ui/switch"
import type { IndexFilterRow } from "@/types/index"

type IndexSettingsDialogProps = {
  dedupe: boolean
  disabled: boolean
  filters: IndexFilterRow[]
  maxDepth: number
  maxDepthId: string
  dedupeId: string
  open: boolean
  onDedupeChange: (value: boolean) => void
  onFiltersChange: (filters: IndexFilterRow[]) => void
  onMaxDepthChange: (value: number) => void
  onOpenChange: (open: boolean) => void
}

const settingHints = {
  maxDepth:
    "How many internal-link hops Atlas follows from the starting URL. Depth 1 only indexes links found on the starting page.",
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
  dedupe,
  dedupeId,
  disabled,
  filters,
  maxDepth,
  maxDepthId,
  open,
  onDedupeChange,
  onFiltersChange,
  onMaxDepthChange,
  onOpenChange,
}: IndexSettingsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[min(760px,calc(100svh-2rem))] overflow-hidden sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Index settings</DialogTitle>
          <DialogDescription>
            Tune how Atlas follows links and filters the final result set.
          </DialogDescription>
        </DialogHeader>

        <div className="grid max-h-[calc(100svh-10rem)] gap-5 overflow-auto pr-1">
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
