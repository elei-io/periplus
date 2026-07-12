import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { useCreateSavedQuery, useUpdateSavedQuery } from "@/hooks/use-saved-queries"
import type { SavedQueryDetail } from "@/types/catalogue"

export function SaveQueryDialog({ open, onOpenChange, sql, query, onSaved }: { open: boolean; onOpenChange: (open: boolean) => void; sql: string; query: SavedQueryDetail | null; onSaved: (query: SavedQueryDetail) => void }) {
  const [name, setName] = useState(query?.name ?? "")
  const [description, setDescription] = useState(query?.description ?? "")
  const [note, setNote] = useState("")
  const create = useCreateSavedQuery()
  const update = useUpdateSavedQuery()

  function reset() {
    setName(query?.name ?? "")
    setDescription(query?.description ?? "")
    setNote("")
  }

  function submit() {
    const options = { onSuccess: (saved: SavedQueryDetail) => { onSaved(saved); reset(); onOpenChange(false) } }
    if (query) update.mutate({ query, sql, name, description, change_note: note }, options)
    else create.mutate({ name, description: description || undefined, sql, change_note: note || undefined }, options)
  }
  const pending = create.isPending || update.isPending
  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) reset(); onOpenChange(nextOpen) }}>
      <DialogContent>
        <DialogHeader><DialogTitle>{query ? `Save revision ${query.current_revision + 1}` : "Save query"}</DialogTitle><DialogDescription>Saved queries keep immutable SQL revision history in Atlas.</DialogDescription></DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5"><Label>Name</Label><Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Latest successful crawls" /></div>
          <div className="grid gap-1.5"><Label>Description</Label><Textarea value={description} onChange={(event) => setDescription(event.target.value)} /></div>
          <div className="grid gap-1.5"><Label>Change note</Label><Input value={note} onChange={(event) => setNote(event.target.value)} placeholder={query ? "What changed?" : "Initial version"} /></div>
        </div>
        <DialogFooter showCloseButton><Button onClick={submit} disabled={!name.trim() || pending}>{pending ? "Saving…" : query ? "Save revision" : "Save query"}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
