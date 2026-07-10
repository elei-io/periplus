import { SlidersHorizontalIcon } from "lucide-react"

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

type ExtractOptionsPopoverProps = {
  disabled: boolean
  extractData: boolean
  extractQueryParams: boolean
  onExtractDataChange: (value: boolean) => void
  onExtractQueryParamsChange: (value: boolean) => void
}

export function ExtractOptionsPopover({
  disabled,
  extractData,
  extractQueryParams,
  onExtractDataChange,
  onExtractQueryParamsChange,
}: ExtractOptionsPopoverProps) {
  const modeLabel = extractData
    ? extractQueryParams
      ? "Records + params"
      : "Records"
    : "Query params"

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
        <span>{modeLabel}</span>
      </PopoverTrigger>

      <PopoverContent
        align="end"
        sideOffset={8}
        className="w-[min(22rem,calc(100vw-2rem))] gap-0 overflow-hidden p-0"
      >
        <PopoverHeader className="border-b px-4 py-3">
          <PopoverTitle>What should Atlas discover?</PopoverTitle>
          <PopoverDescription>
            Choose one or both outputs for this run.
          </PopoverDescription>
        </PopoverHeader>

        <div className="grid gap-2 p-3">
          <ModeSwitch
            checked={extractData}
            description="Turn repeated page content into structured records."
            disabled={disabled || (extractData && !extractQueryParams)}
            label="Records"
            onChange={onExtractDataChange}
          />
          <ModeSwitch
            checked={extractQueryParams}
            description="Find filters, sorting, pagination, and other URL controls."
            disabled={disabled || (extractQueryParams && !extractData)}
            label="Query parameters"
            onChange={onExtractQueryParamsChange}
          />
        </div>
      </PopoverContent>
    </Popover>
  )
}

function ModeSwitch({
  checked,
  description,
  disabled,
  label,
  onChange,
}: {
  checked: boolean
  description: string
  disabled: boolean
  label: string
  onChange: (value: boolean) => void
}) {
  return (
    <label className="flex items-center justify-between gap-4 rounded-xl border bg-muted/10 p-3">
      <span className="grid gap-0.5">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-[11px] leading-4 text-muted-foreground">
          {description}
        </span>
      </span>
      <Switch
        checked={checked}
        disabled={disabled}
        onCheckedChange={onChange}
      />
    </label>
  )
}
