import { AlertTriangleIcon, BracesIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import type { QualityWarning } from "@/types/extract"

type QualityWarningsProps = {
  emptyMessage?: string
  warnings: QualityWarning[]
}

export function QualityWarnings({
  emptyMessage = "No quality warnings were reported.",
  warnings,
}: QualityWarningsProps) {
  if (warnings.length === 0) {
    return (
      <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
        {emptyMessage}
      </div>
    )
  }

  return (
    <div className="min-h-0 flex-1 space-y-2 overflow-auto rounded-md border p-2">
      {warnings.map((warning) => (
        <div key={warning.code} className="grid gap-2 rounded-md p-2">
          <div className="flex items-center gap-2">
            <AlertTriangleIcon className="size-4 text-amber-400" />
            <h2 className="text-sm font-medium">{warning.name}</h2>
            <Badge variant="outline">{warning.code}</Badge>
          </div>
          <p className="text-xs leading-5 text-muted-foreground">
            {warning.description}
          </p>
          {warning.signals.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {warning.signals.map((signal) => (
                <Badge key={signal.name} variant="secondary">
                  <BracesIcon />
                  {signal.name}: {String(signal.value)}
                </Badge>
              ))}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  )
}
