import { useState } from "react"

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
import { useCreateCatalogueScalarMacro } from "@/hooks/use-catalogue-scalar-macros"

export function SaveScalarMacroDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [slug, setSlug] = useState("")
  const [description, setDescription] = useState("")
  const [parameters, setParameters] = useState("")
  const [sql, setSql] = useState("value")
  const create = useCreateCatalogueScalarMacro()

  function reset() {
    setSlug("")
    setDescription("")
    setParameters("")
    setSql("value")
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset()
        onOpenChange(next)
      }}
    >
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Create scalar macro</DialogTitle>
          <DialogDescription>
            Define a reusable scalar SQL expression in the macros schema.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="scalar-macro-slug">Slug</Label>
              <Input
                id="scalar-macro-slug"
                value={slug}
                onChange={(event) =>
                  setSlug(
                    event.target.value
                      .toLowerCase()
                      .replace(/[^a-z0-9_-]/g, "-")
                  )
                }
                placeholder="normalized-price"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="scalar-macro-parameters">Parameters</Label>
              <Input
                id="scalar-macro-parameters"
                value={parameters}
                onChange={(event) => setParameters(event.target.value)}
                placeholder="value, currency"
              />
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="scalar-macro-description">Description</Label>
            <Textarea
              id="scalar-macro-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <div className="overflow-hidden rounded-xl border bg-card">
            <div className="border-b bg-muted/20 px-3 py-2 text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
              Scalar expression
            </div>
            <SqlEditor
              value={sql}
              onChange={setSql}
              height="220px"
              ariaLabel="SQL expression for the new scalar macro"
            />
          </div>
        </div>
        <DialogFooter showCloseButton>
          <Button
            disabled={!slug || !sql.trim() || create.isPending}
            onClick={() =>
              create.mutate(
                {
                  slug,
                  description: description || undefined,
                  parameters: parameters
                    .split(",")
                    .map((value) => value.trim())
                    .filter(Boolean),
                  sql,
                },
                {
                  onSuccess: () => {
                    reset()
                    onOpenChange(false)
                  },
                }
              )
            }
          >
            {create.isPending ? "Creating…" : "Create scalar macro"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
