import { CircleHelpIcon, Trash2Icon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { IndexFilterRow, IndexFilterType } from "@/types/index"

export type SelectOption<TValue extends string> = {
  value: TValue
  label: string
}

const filterTypes: Array<SelectOption<IndexFilterType>> = [
  { value: "include_crawl", label: "Include crawl" },
  { value: "exclude_crawl", label: "Exclude crawl" },
  { value: "include_result", label: "Include result" },
  { value: "exclude_result", label: "Exclude result" },
]

const filterHints = {
  filterType:
    "Choose whether this pattern affects crawled pages or only final results.",
  filterValue: "A glob pattern such as https://example.com/docs/*.",
}

type SelectFieldProps<TValue extends string> = {
  label: string
  hint: string
  value: TValue
  disabled: boolean
  options: Array<SelectOption<TValue>>
  onChange: (value: TValue) => void
}

export function SelectField<TValue extends string>({
  label,
  hint,
  value,
  disabled,
  options,
  onChange,
}: SelectFieldProps<TValue>) {
  const selectedLabel =
    options.find((option) => option.value === value)?.label ?? value

  return (
    <div className="grid gap-2">
      <FieldLabel hint={hint}>{label}</FieldLabel>
      <Select
        value={value}
        onValueChange={(nextValue) => {
          if (nextValue !== null) {
            onChange(nextValue as TValue)
          }
        }}
        disabled={disabled}
      >
        <SelectTrigger className="w-full">
          <span>{selectedLabel}</span>
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

type NumberSliderProps = {
  id: string
  label: string
  hint: string
  min: number
  max: number
  value: number
  disabled: boolean
  onChange: (value: number) => void
}

export function NumberSlider({
  id,
  label,
  hint,
  min,
  max,
  value,
  disabled,
  onChange,
}: NumberSliderProps) {
  return (
    <div className="grid gap-2">
      <div className="flex items-center justify-between gap-3">
        <FieldLabel htmlFor={id} hint={hint}>
          {label}
        </FieldLabel>
        <Input
          id={id}
          className="h-8 w-20"
          type="number"
          min={min}
          max={max}
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
          disabled={disabled}
        />
      </div>
      <Slider
        min={min}
        max={max}
        step={1}
        value={[value]}
        onValueChange={(nextValue) =>
          onChange(Array.isArray(nextValue) ? (nextValue[0] ?? min) : nextValue)
        }
        disabled={disabled}
      />
    </div>
  )
}

type FilterEditorProps = {
  filter: IndexFilterRow
  disabled: boolean
  onChange: (filter: IndexFilterRow) => void
  onRemove: () => void
}

export function FilterEditor({
  filter,
  disabled,
  onChange,
  onRemove,
}: FilterEditorProps) {
  const selectedFilterLabel =
    filterTypes.find((filterType) => filterType.value === filter.type)?.label ??
    filter.type

  return (
    <div className="grid gap-2 rounded-lg border p-2 md:grid-cols-[190px_minmax(0,1fr)_auto]">
      <div className="grid gap-1.5">
        <FieldLabel hint={filterHints.filterType}>Type</FieldLabel>
        <Select
          value={filter.type}
          onValueChange={(value) => {
            if (value !== null) {
              onChange({ ...filter, type: value as IndexFilterType })
            }
          }}
          disabled={disabled}
        >
          <SelectTrigger className="w-full">
            <span>{selectedFilterLabel}</span>
          </SelectTrigger>
          <SelectContent>
            {filterTypes.map((filterType) => (
              <SelectItem key={filterType.value} value={filterType.value}>
                {filterType.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="grid gap-1.5">
        <FieldLabel hint={filterHints.filterValue}>Pattern</FieldLabel>
        <Input
          value={filter.value}
          placeholder="https://example.com/docs/*"
          onChange={(event) =>
            onChange({ ...filter, value: event.target.value })
          }
          disabled={disabled}
        />
      </div>
      <Button
        className="self-end"
        type="button"
        variant="ghost"
        size="icon"
        onClick={onRemove}
        disabled={disabled}
      >
        <Trash2Icon />
        <span className="sr-only">Remove filter</span>
      </Button>
    </div>
  )
}

type FieldLabelProps = {
  children: string
  hint: string
  htmlFor?: string
}

export function FieldLabel({ children, hint, htmlFor }: FieldLabelProps) {
  return (
    <div className="flex items-center gap-2">
      <Label htmlFor={htmlFor}>{children}</Label>
      <InfoTooltip>{hint}</InfoTooltip>
    </div>
  )
}

type InfoTooltipProps = {
  children: string
}

export function InfoTooltip({ children }: InfoTooltipProps) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            className="size-5 text-muted-foreground"
            tabIndex={-1}
            type="button"
            variant="ghost"
            size="icon-xs"
          />
        }
      >
        <CircleHelpIcon />
        <span className="sr-only">More information</span>
      </TooltipTrigger>
      <TooltipContent side="top" align="start">
        {children}
      </TooltipContent>
    </Tooltip>
  )
}
