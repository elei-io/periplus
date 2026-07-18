import { DatabaseZapIcon } from "lucide-react"

import { CatalogueMaterializationDetail } from "@/pages/catalogue/materialization-detail-page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import type {
  CatalogueMaterializationRecord,
  CatalogueMaterializationSummary,
} from "@/types/catalogue"

export function MaterializationSheet({
  materialization,
  status,
}: {
  materialization: CatalogueMaterializationRecord | undefined
  status: CatalogueMaterializationSummary["status"]
}) {
  return (
    <Sheet>
      <SheetTrigger render={<Button size="sm" variant="outline" />}>
        <DatabaseZapIcon />
        Materialization
        <StatusBadge status={status} />
      </SheetTrigger>
      <SheetContent
        side="bottom"
        overlayClassName="bg-black/35 backdrop-blur-none"
        className="h-[70svh] rounded-t-2xl"
      >
        <SheetHeader className="border-b pr-14">
          <SheetTitle>Materialization</SheetTitle>
          <SheetDescription>
            Configure how Atlas keeps this view materialized.
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {materialization ? (
            <CatalogueMaterializationDetail materialization={materialization} />
          ) : (
            <div className="p-6 text-sm text-muted-foreground">
              Loading materialization details…
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

function StatusBadge({
  status,
}: {
  status: CatalogueMaterializationSummary["status"]
}) {
  const label =
    status === "backfilling"
      ? "Backfilling"
      : status === "source_changed"
        ? "Source changed"
        : status[0].toUpperCase() + status.slice(1)
  return (
    <Badge
      variant={
        status === "dematerializing" || status === "source_changed"
          ? "destructive"
          : status === "live" || status === "backfilling"
            ? "default"
            : "secondary"
      }
    >
      {label}
    </Badge>
  )
}
