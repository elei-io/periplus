import { useState } from "react"
import { PauseIcon, PlayIcon, Trash2Icon } from "lucide-react"

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
  useUpdateCatalogueMaterialization,
} from "@/hooks/use-catalogue-materializations"
import type { CatalogueMaterializationRecord } from "@/types/catalogue"

export function CatalogueMaterializationDetail({
  materialization,
}: {
  materialization: CatalogueMaterializationRecord
}) {
  const update = useUpdateCatalogueMaterialization()
  const [delay, setDelay] = useState(materialization.refresh_delay_seconds)
  const [dematerializeOpen, setDematerializeOpen] = useState(false)
  const paused = materialization.desired_state === "paused"
  const terminal = ["deleting", "blocked_schema", "failed"].includes(
    materialization.observed_state
  )
  const building = ["creating", "backfilling"].includes(
    materialization.observed_state
  )

  return (
    <>
      <div className="grid gap-6 p-5">
        <section>
          <h3 className="mb-1 text-sm font-medium">CDC maintenance</h3>
          <div className="divide-y">
            <div className="flex items-center justify-between gap-3 py-3">
              <div>
                <div className="text-sm">Driving table</div>
                <code className="text-xs text-muted-foreground">
                  main.{materialization.source_table}
                </code>
              </div>
              <span className="text-xs text-muted-foreground">
                {materialization.observed_state}
              </span>
            </div>
            {materialization.bootstrap_partition_count !== null ? (
              <div className="flex items-center justify-between gap-3 py-3">
                <div>
                  <div className="text-sm">Historical backfill</div>
                  <div className="text-xs text-muted-foreground">
                    Live CDC remains active between bounded batches
                  </div>
                </div>
                <span className="text-xs text-muted-foreground">
                  {materialization.bootstrap_partition_cursor ?? 0} /{" "}
                  {materialization.bootstrap_partition_count} partitions
                </span>
              </div>
            ) : null}
            <div className="flex items-center justify-between gap-3 py-3">
              <div>
                <div className="text-sm">Refresh strategy</div>
                <div className="text-xs text-muted-foreground">
                  {materialization.refresh_strategy === "keyed"
                    ? `Replace by (${materialization.key_columns.join(", ")})`
                    : materialization.refresh_strategy === "append"
                      ? `Append by (${materialization.key_columns.join(", ")})`
                      : "Full rebuild"}
                </div>
              </div>
            </div>
            <div className="flex items-center justify-between gap-3 py-3">
              <div>
                <div className="text-sm">Refresh consumption</div>
                <div className="text-xs text-muted-foreground">
                  Pausing leaves the durable NATS cursor in place
                </div>
              </div>
              <Button
                size="sm"
                variant={paused ? "default" : "outline"}
                onClick={() =>
                  update.mutate({
                    id: materialization.id,
                    desired_state: paused ? "live" : "paused",
                  })
                }
                disabled={update.isPending || terminal || building}
              >
                {paused ? <PlayIcon /> : <PauseIcon />}
                {paused ? "Continue" : "Pause"}
              </Button>
            </div>
            <div className="flex items-center justify-between gap-3 py-3">
              <div>
                <div className="text-sm">Coalescing delay</div>
                <div className="text-xs text-muted-foreground">
                  More delay combines more ticks into one refresh transaction
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Input
                  className="h-8 w-24"
                  type="number"
                  min={0}
                  max={3600}
                  step={0.1}
                  value={delay}
                  onChange={(event) => setDelay(Number(event.target.value) || 0)}
                />
                <span className="text-xs text-muted-foreground">seconds</span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={
                    update.isPending ||
                    delay === materialization.refresh_delay_seconds
                  }
                  onClick={() =>
                    update.mutate({
                      id: materialization.id,
                      refresh_delay_seconds: delay,
                    })
                  }
                >
                  Save
                </Button>
              </div>
            </div>
          </div>
          {materialization.last_error ? (
            <p className="mt-3 rounded-md bg-destructive/10 p-3 text-xs text-destructive">
              {materialization.last_error}
            </p>
          ) : null}
        </section>
        <div className="flex justify-end border-t pt-4">
          <Button
            size="sm"
            variant="destructive"
            onClick={() => setDematerializeOpen(true)}
            disabled={materialization.desired_state === "deleting"}
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
            Atlas will delete its NATS consumer, drop the stored table, and
            restore the original virtual view.
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
