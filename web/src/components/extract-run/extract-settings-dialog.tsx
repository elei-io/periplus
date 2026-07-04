import {
  FieldLabel,
  SelectField,
} from "@/components/index-run/index-settings-fields"
import type { SelectOption } from "@/components/index-run/index-settings-fields"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"
import type { ExtractSchemaType } from "@/types/extract"
import type { CrawlMode, CrawlWait } from "@/types/index"

type ExtractSettingsDialogProps = {
  disabled: boolean
  mode: CrawlMode
  open: boolean
  schemaType: ExtractSchemaType
  targetJsonExample: string
  targetJsonExampleId: string
  wait: CrawlWait
  onModeChange: (value: CrawlMode) => void
  onOpenChange: (open: boolean) => void
  onSchemaTypeChange: (value: ExtractSchemaType) => void
  onTargetJsonExampleChange: (value: string) => void
  onWaitChange: (value: CrawlWait) => void
}

const schemaTypes: Array<SelectOption<ExtractSchemaType>> = [
  { value: "css", label: "CSS" },
  { value: "xpath", label: "XPath" },
]

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
  mode: "Controls how Atlas loads the page before extraction. Static is fastest, dynamic handles richer pages, and app is best for client-rendered sites.",
  schemaType:
    "Controls the selector format generated for Crawl4AI. CSS is a good default; XPath can help with deeply nested or awkward markup.",
  targetJsonExample:
    "Optional JSON object that teaches the schema generator the exact result shape you want.",
  wait: "Controls when Atlas decides the page is ready before extracting. Stable is useful for pages that hydrate slowly.",
}

export function ExtractSettingsDialog({
  disabled,
  mode,
  open,
  schemaType,
  targetJsonExample,
  targetJsonExampleId,
  wait,
  onModeChange,
  onOpenChange,
  onSchemaTypeChange,
  onTargetJsonExampleChange,
  onWaitChange,
}: ExtractSettingsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[min(720px,calc(100svh-2rem))] overflow-hidden sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Extract settings</DialogTitle>
          <DialogDescription>
            Tune page loading and schema generation for this one-off extraction.
          </DialogDescription>
        </DialogHeader>

        <div className="grid max-h-[calc(100svh-10rem)] gap-5 overflow-auto pr-1">
          <div className="grid gap-4 md:grid-cols-3">
            <SelectField
              label="Schema"
              hint={settingHints.schemaType}
              value={schemaType}
              disabled={disabled}
              options={schemaTypes}
              onChange={onSchemaTypeChange}
            />
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

          <div className="grid gap-2">
            <FieldLabel
              htmlFor={targetJsonExampleId}
              hint={settingHints.targetJsonExample}
            >
              Target JSON example
            </FieldLabel>
            <Textarea
              id={targetJsonExampleId}
              className="min-h-36 resize-y font-mono text-xs"
              value={targetJsonExample}
              placeholder={
                '{\n  "title": "Example title",\n  "price": "$10"\n}'
              }
              onChange={(event) =>
                onTargetJsonExampleChange(event.target.value)
              }
              disabled={disabled}
            />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
