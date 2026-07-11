import { useEffect, useState } from "react"

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
import { Textarea } from "@/components/ui/textarea"
import { useCreateCatalogueView } from "@/hooks/use-catalogue-views"

export function SaveViewDialog({ open, onOpenChange, sql }: { open: boolean; onOpenChange: (open: boolean) => void; sql: string }) {
  const [name, setName] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [description, setDescription] = useState("")
  const create = useCreateCatalogueView()

  useEffect(() => {
    if (!open) return
    setName("")
    setDisplayName("")
    setDescription("")
  }, [open])

  function submit() {
    create.mutate(
      { name, display_name: displayName || undefined, description: description || undefined, sql },
      { onSuccess: () => onOpenChange(false) }
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save as view</DialogTitle>
          <DialogDescription>Create a persistent DuckLake view in the views schema.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="view-name">View name</Label>
            <Input id="view-name" value={name} onChange={(event) => setName(event.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))} placeholder="follower_mentions" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="view-display-name">Display name</Label>
            <Input id="view-display-name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Follower mentions" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="view-description">Description</Label>
            <Textarea id="view-description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What this view is useful for" />
          </div>
          <div className="rounded-md border bg-muted/30 p-2 font-mono text-[10px] text-muted-foreground">
            {sql.slice(0, 500)}{sql.length > 500 ? "…" : ""}
          </div>
        </div>
        <DialogFooter showCloseButton>
          <Button onClick={submit} disabled={!name || create.isPending}>{create.isPending ? "Creating…" : "Create view"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
