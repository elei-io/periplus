import { SelectField } from "@/components/index-run/index-settings-fields"
import type { SelectOption } from "@/components/index-run/index-settings-fields"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import type { CrawlMode, CrawlWait } from "@/types/index"

type ScrapeSettingsDialogProps = {
  disabled: boolean
  mode: CrawlMode
  open: boolean
  wait: CrawlWait
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
  mode: "Controls how Atlas loads the page. Static is fastest, dynamic handles richer pages, and app is best for client-rendered sites.",
  wait: "Controls when Atlas decides the page is ready before returning HTML and Crawl4AI metadata.",
}

export function ScrapeSettingsDialog({
  disabled,
  mode,
  open,
  wait,
  onModeChange,
  onOpenChange,
  onWaitChange,
}: ScrapeSettingsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Scrape settings</DialogTitle>
          <DialogDescription>
            Tune how Atlas loads the page before returning HTML and crawl
            metadata.
          </DialogDescription>
        </DialogHeader>

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
      </DialogContent>
    </Dialog>
  )
}
