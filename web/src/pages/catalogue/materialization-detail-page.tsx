import { useState } from "react"
import { PauseIcon, PlayIcon, RefreshCwIcon, Trash2Icon } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  useDematerialize,
  useRebuildCatalogueMaterialization,
  useUpdateCatalogueMaterializationMaintenance,
} from "@/hooks/use-catalogue-materializations"
import type { CatalogueMaterializationRecord } from "@/types/catalogue"

export function CatalogueMaterializationDetail({
  materialization,
}: {
  materialization: CatalogueMaterializationRecord
}) {
  const maintenance = useUpdateCatalogueMaterializationMaintenance()
  const rebuild = useRebuildCatalogueMaterialization()
  const [rate, setRate] = useState(materialization.backfill_scopes_per_minute)
  const [dematerializeOpen, setDematerializeOpen] = useState(false)

  return (
    <>
      <div className="grid gap-6 p-5">
        <section>
          <h3 className="mb-1 text-sm font-medium">Maintenance</h3>
          <div className="divide-y">
            <div className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div>
                <div className="text-sm">Incremental discriminator</div>
                <div className="text-xs text-muted-foreground">
                  {{
                    url: "URL",
                    document: "Document",
                    crawl: "Crawl",
                  }[materialization.scope_kind]}{" "}
                  · <code>{materialization.scope_column}</code>
                </div>
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div>
                <div className="text-sm">New data</div>
                <div className="text-xs text-muted-foreground">
                  Process newly discovered scopes as they arrive
                </div>
              </div>
              <Button
                size="sm"
                variant={materialization.live_enabled ? "outline" : "default"}
                onClick={() =>
                  maintenance.mutate({
                    id: materialization.id,
                    live_enabled: !materialization.live_enabled,
                  })
                }
                disabled={
                  maintenance.isPending ||
                  materialization.status === "dematerializing"
                }
              >
                {materialization.live_enabled ? <PauseIcon /> : <PlayIcon />}
                {materialization.live_enabled ? "Pause" : "Resume"}
              </Button>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div>
                <div className="text-sm">Existing data</div>
                <div className="text-xs text-muted-foreground">
                  {materialization.backfill_enabled
                    ? "Backfill is enabled"
                    : "Backfill is paused"}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Input
                  aria-label="Backfill units per minute"
                  className="h-8 w-24"
                  type="number"
                  min={1}
                  max={10_000}
                  value={rate}
                  onChange={(event) => setRate(Number(event.target.value) || 1)}
                />
                <span className="text-xs text-muted-foreground">/ min</span>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    maintenance.mutate({
                      id: materialization.id,
                      backfill_scopes_per_minute: rate,
                    })
                  }
                  disabled={
                    maintenance.isPending ||
                    rate === materialization.backfill_scopes_per_minute
                  }
                >
                  Save
                </Button>
                <Button
                  size="sm"
                  variant={
                    materialization.backfill_enabled ? "outline" : "default"
                  }
                  onClick={() =>
                    maintenance.mutate({
                      id: materialization.id,
                      backfill_enabled: !materialization.backfill_enabled,
                    })
                  }
                  disabled={
                    maintenance.isPending ||
                    materialization.status === "dematerializing"
                  }
                >
                  {materialization.backfill_enabled ? (
                    <PauseIcon />
                  ) : (
                    <PlayIcon />
                  )}
                  {materialization.backfill_enabled ? "Pause" : "Resume"}
                </Button>
              </div>
            </div>
          </div>
        </section>

        <div className="flex flex-wrap justify-end gap-2 border-t pt-4">
          <Button
            size="sm"
            variant="outline"
            onClick={() => rebuild.mutate({ materialization })}
            disabled={
              rebuild.isPending || materialization.status === "dematerializing"
            }
          >
            <RefreshCwIcon />
            {rebuild.isPending ? "Starting…" : "Rebuild"}
          </Button>
          <Button
            size="sm"
            variant="destructive"
            onClick={() => setDematerializeOpen(true)}
            disabled={materialization.status === "dematerializing"}
          >
            <Trash2Icon />
            Turn off materialization
          </Button>
        </div>
      </div>
      <DematerializeDialog
        materialization={materialization}
        open={dematerializeOpen}
        onOpenChange={setDematerializeOpen}
      />
    </>
  )
}

function DematerializeDialog({
  materialization,
  open,
  onOpenChange,
}: {
  materialization: CatalogueMaterializationRecord
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [confirmation, setConfirmation] = useState("")
  const dematerialize = useDematerialize()
  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) setConfirmation("")
        onOpenChange(nextOpen)
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            Turn off materialization for {materialization.display_name}?
          </DialogTitle>
          <DialogDescription>
            The view remains available, but reads will evaluate its SQL
            directly. Atlas will stop maintenance and remove the stored results.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-1.5">
          <Label>Type {materialization.name} to confirm</Label>
          <Input
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
          />
        </div>
        <DialogFooter showCloseButton>
          <Button
            variant="destructive"
            disabled={
              confirmation !== materialization.name || dematerialize.isPending
            }
            onClick={() =>
              dematerialize.mutate(materialization, {
                onSuccess: () => onOpenChange(false),
              })
            }
          >
            {dematerialize.isPending
              ? "Turning off…"
              : "Turn off materialization"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
