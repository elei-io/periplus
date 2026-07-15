import { useState } from "react"

import { formatSql } from "@/components/catalogue/sql-format"
import { SqlEditor } from "@/components/catalogue/sql-editor"
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
import { useCreateCatalogueTableMacro } from "@/hooks/use-catalogue-table-macros"

export function SaveTableMacroDialog({
  open,
  onOpenChange,
  sql,
  queryRevisionId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  sql: string
  queryRevisionId?: string
}) {
  const [name, setName] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [description, setDescription] = useState("")
  const [parameters, setParameters] = useState("")
  const create = useCreateCatalogueTableMacro()

  function reset() {
    setName("")
    setDisplayName("")
    setDescription("")
    setParameters("")
  }

  function submit() {
    create.mutate(
      {
        name,
        display_name: displayName || undefined,
        description: description || undefined,
        parameters: parseParameters(parameters),
        sql,
        created_from_query_revision_id: queryRevisionId,
      },
      {
        onSuccess: () => {
          reset()
          onOpenChange(false)
        },
      }
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) reset()
        onOpenChange(nextOpen)
      }}
    >
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Save as table macro</DialogTitle>
          <DialogDescription>
            Turn this query into a reusable, parameterized relation in the
            macros schema.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="macro-name">Macro name</Label>
              <Input
                id="macro-name"
                value={name}
                onChange={(event) =>
                  setName(
                    event.target.value
                      .toLowerCase()
                      .replace(/[^a-z0-9_]/g, "_")
                  )
                }
                placeholder="selector_stats"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="macro-parameters">Parameters</Label>
              <Input
                id="macro-parameters"
                value={parameters}
                onChange={(event) => setParameters(event.target.value)}
                placeholder="hostname, path_pattern"
              />
              <p className="text-[10px] text-muted-foreground">
                Comma-separated SQL identifiers used by the query body.
              </p>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="macro-display-name">Display name</Label>
              <Input
                id="macro-display-name"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="Selector statistics"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="macro-description">Description</Label>
              <Textarea
                id="macro-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="What this macro returns"
                className="min-h-9"
              />
            </div>
          </div>
          <div className="overflow-hidden rounded-xl border bg-card">
            <div className="border-b bg-muted/20 px-3 py-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              Table macro query · read only
            </div>
            <SqlEditor
              value={formatSql(sql)}
              readOnly
              height="180px"
              ariaLabel="Read-only SQL for the new table macro"
            />
          </div>
        </div>
        <DialogFooter showCloseButton>
          <Button onClick={submit} disabled={!name || create.isPending}>
            {create.isPending ? "Creating…" : "Create table macro"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function parseParameters(value: string): string[] {
  return value
    .split(",")
    .map((parameter) => parameter.trim())
    .filter(Boolean)
}
