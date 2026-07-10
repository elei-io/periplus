import { PlusIcon, SlidersHorizontalIcon } from "lucide-react"

import {
  FieldLabel,
  FilterEditor,
  InfoTooltip,
} from "@/components/index-run/index-settings-fields"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Switch } from "@/components/ui/switch"
import type { IndexFilterRow } from "@/types/index"

type IndexOptionsPopoverProps = {
  dedupe: boolean
  dedupeId: string
  disabled: boolean
  filters: IndexFilterRow[]
  maxDepth: number
  onDedupeChange: (value: boolean) => void
  onFiltersChange: (filters: IndexFilterRow[]) => void
  onMaxDepthChange: (value: number) => void
}

const settingHints = {
  maxDepth:
    "How many internal-link hops Atlas follows from the starting URL. Depth 0 only inspects the starting page.",
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

export function IndexOptionsPopover({
  dedupe,
  dedupeId,
  disabled,
  filters,
  maxDepth,
  onDedupeChange,
  onFiltersChange,
  onMaxDepthChange,
}: IndexOptionsPopoverProps) {
  const extraOptionCount =
    Number(dedupe) + filters.filter((filter) => filter.value.trim()).length

  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button
            className="h-9 rounded-full px-3 text-muted-foreground hover:bg-muted/60 hover:text-foreground"
            type="button"
            variant="ghost"
            disabled={disabled}
          />
        }
      >
        <SlidersHorizontalIcon />
        <span>Depth {maxDepth}</span>
        {extraOptionCount > 0 ? (
          <span className="flex size-4 items-center justify-center rounded-full bg-primary text-[10px] font-medium text-primary-foreground">
            {extraOptionCount}
          </span>
        ) : null}
      </PopoverTrigger>

      <PopoverContent
        align="end"
        sideOffset={8}
        className="w-[min(26rem,calc(100vw-2rem))] gap-0 overflow-hidden p-0"
      >
        <PopoverHeader className="border-b px-4 py-3">
          <PopoverTitle>Index options</PopoverTitle>
          <PopoverDescription>
            Choose how far Atlas follows links and what it keeps.
          </PopoverDescription>
        </PopoverHeader>

        <div className="grid max-h-[min(32rem,calc(100svh-10rem))] gap-4 overflow-auto p-4">
          <div className="grid gap-2.5">
            <FieldLabel hint={settingHints.maxDepth}>Crawl depth</FieldLabel>
            <div className="grid grid-cols-6 gap-1.5">
              {[0, 1, 2, 3, 4, 5].map((depth) => (
                <Button
                  key={depth}
                  type="button"
                  size="sm"
                  variant={maxDepth === depth ? "default" : "outline"}
                  disabled={disabled}
                  onClick={() => onMaxDepthChange(depth)}
                  aria-label={`Set crawl depth to ${depth}`}
                >
                  {depth}
                </Button>
              ))}
            </div>
            <p className="text-[11px] text-muted-foreground">
              {depthDescription(maxDepth)}
            </p>
          </div>

          <div className="flex items-center justify-between gap-4 rounded-lg border bg-muted/15 p-3">
            <div className="grid gap-0.5">
              <FieldLabel htmlFor={dedupeId} hint={settingHints.dedupe}>
                Dedupe URLs
              </FieldLabel>
              <p className="text-[11px] text-muted-foreground">
                Keep the shallowest instance of each link.
              </p>
            </div>
            <Switch
              id={dedupeId}
              checked={dedupe}
              onCheckedChange={onDedupeChange}
              disabled={disabled}
            />
          </div>

          <div className="grid gap-2.5 border-t pt-4">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">URL filters</span>
                <InfoTooltip>{settingHints.filters}</InfoTooltip>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => onFiltersChange([...filters, createFilter()])}
                disabled={disabled}
              >
                <PlusIcon />
                Add filter
              </Button>
            </div>

            {filters.length === 0 ? (
              <p className="rounded-lg border border-dashed bg-muted/10 p-3 text-[11px] leading-5 text-muted-foreground">
                No filters. Atlas can follow every discovered internal link and
                return every match.
              </p>
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
      </PopoverContent>
    </Popover>
  )
}

function depthDescription(depth: number) {
  if (depth === 0) return "Inspect the starting page without following links."
  if (depth === 1) return "Follow links found on the starting page."
  return `Follow internal links up to ${depth} hops from the starting page.`
}
