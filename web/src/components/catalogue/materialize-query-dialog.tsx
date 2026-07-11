import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { useCreateMaterializedView } from "@/hooks/use-materialized-views"
import type { SavedQueryRevision } from "@/types/catalogue"
export function MaterializeQueryDialog({ open, onOpenChange, revision }: { open: boolean; onOpenChange: (open: boolean) => void; revision: SavedQueryRevision }) {
  const [name, setName] = useState(""); const [displayName, setDisplayName] = useState(""); const [description, setDescription] = useState(""); const create = useCreateMaterializedView()
  useEffect(() => { if (open) { setName(""); setDisplayName(""); setDescription("") } }, [open])
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogHeader><DialogTitle>Materialize revision {revision.revision}</DialogTitle><DialogDescription>Create a durable DuckLake table in the materialized schema. Version one uses explicit full refreshes.</DialogDescription></DialogHeader><div className="grid gap-3"><div className="grid gap-1.5"><Label>Table name</Label><Input value={name} onChange={(event) => setName(event.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))} placeholder="latest_crawls" /></div><div className="grid gap-1.5"><Label>Display name</Label><Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></div><div className="grid gap-1.5"><Label>Description</Label><Textarea value={description} onChange={(event) => setDescription(event.target.value)} /></div></div><DialogFooter showCloseButton><Button disabled={!name || create.isPending} onClick={() => create.mutate({ name, display_name: displayName || undefined, description: description || undefined, query_revision_id: revision.id }, { onSuccess: () => onOpenChange(false) })}>{create.isPending ? "Materializing…" : "Create materialized view"}</Button></DialogFooter></DialogContent></Dialog>
}
