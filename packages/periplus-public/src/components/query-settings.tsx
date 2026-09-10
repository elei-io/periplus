"use client"

import { useState } from "react"
import { Plus, Settings2, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Separator } from "@/components/ui/separator"
import { parameterDraft, parameterValue, pasteParameters } from "@/lib/query-parameters"
import type { ParameterDraft, ParameterKind } from "@/lib/query-parameters"

const kinds: { value: ParameterKind; label: string }[] = [{ value: "text", label: "Text" }, { value: "number", label: "Number" }, { value: "boolean", label: "Boolean" }, { value: "null", label: "NULL" }, { value: "json", label: "JSON" }]
function readParameters(value: string) {
  try {
    const values: unknown = JSON.parse(value)
    if (!Array.isArray(values)) throw new Error()
    return { rows: values.map(parameterDraft), error: "" }
  } catch { return { rows: [], error: "The supplied parameters are invalid. Add replacement values or apply an empty list." } }
}

export function QuerySettings({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<ParameterDraft[]>([])
  const [importError, setImportError] = useState("")
  const current = readParameters(value)
  const visibleRows: ParameterDraft[] = rows.length ? rows : [{ kind: "text", value: "" }]
  const errors = rows.map(row => { try { parameterValue(row); return "" } catch (error) { return (error as Error).message } })
  function update(index: number, patch: Partial<ParameterDraft>) { setImportError(""); setRows(visibleRows.map((row, i) => i === index ? { ...row, ...patch } : row)) }

  return <Dialog open={open} onOpenChange={next => { if (next) { setRows(current.rows); setImportError("") } setOpen(next) }}>
    <DialogTrigger render={<Button variant="ghost" />}><Settings2 />Query settings{current.rows.length > 0 && <Badge variant="secondary">{current.rows.length}</Badge>}{current.error && <Badge variant="destructive">Invalid parameters</Badge>}</DialogTrigger>
    <DialogContent className="max-h-[85svh] overflow-y-auto sm:max-w-2xl">
      <DialogHeader><DialogTitle>Query parameters</DialogTitle><DialogDescription>Enter a value for each ? placeholder in your SQL, in the same order.</DialogDescription></DialogHeader>
      <Separator />
      {current.error && <p role="alert" className="text-destructive">{current.error}</p>}
      <div className="flex flex-col gap-3">
        {visibleRows.map((row, index) => <div key={index} className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{index + 1}</Badge>
            <Select value={row.kind} onValueChange={kind => { if (kind) update(index, { kind, value: kind === "boolean" ? "true" : kind === "null" ? "" : row.value }) }}><SelectTrigger className="w-24" aria-label={`Parameter ${index + 1} type`}><SelectValue>{kinds.find(kind => kind.value === row.kind)?.label}</SelectValue></SelectTrigger><SelectContent>{kinds.map(kind => <SelectItem key={kind.value} value={kind.value}>{kind.label}</SelectItem>)}</SelectContent></Select>
            {row.kind === "boolean" ? <Select value={row.value} onValueChange={next => { if (next) update(index, { value: next }) }}><SelectTrigger className="min-w-32 flex-1" aria-label={`Parameter ${index + 1} value`}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="true">true</SelectItem><SelectItem value="false">false</SelectItem></SelectContent></Select> : <Input className="min-w-32 flex-1 font-mono" aria-label={`Parameter ${index + 1} value`} aria-invalid={Boolean(errors[index])} aria-describedby={errors[index] ? `parameter-error-${index}` : undefined} disabled={row.kind === "null"} placeholder={row.kind === "null" ? "NULL" : "Value"} value={row.value} onChange={event => update(index, { value: event.target.value })} onPaste={event => {
              if (row.kind === "json") return
              const text = event.clipboardData.getData("text")
              if (!/[,\r\n]/.test(text)) return
              try {
                const imported = pasteParameters(text)
                if (imported.length < 2) return
                event.preventDefault()
                setRows([...visibleRows.slice(0, index), ...imported, ...visibleRows.slice(index + 1)])
                setImportError("")
              } catch (error) {
                event.preventDefault()
                setImportError((error as Error).message)
              }
            }} />}
            <Button variant="ghost" size="icon-sm" aria-label={`Remove parameter ${index + 1}`} onClick={() => setRows(previous => previous.filter((_, i) => i !== index))}><Trash2 /></Button>
          </div>
          {errors[index] && <p id={`parameter-error-${index}`} role="alert" className="text-destructive">{errors[index]}</p>}
        </div>)}
        <Button variant="outline" className="self-start" onClick={() => setRows(previous => [...previous, { kind: "text", value: "" }])}><Plus />Add parameter</Button>
      </div>
      <Separator />
      <p className="text-muted-foreground">Paste comma- or newline-separated values into an input to create multiple parameters. Quote text containing separators. Types are detected automatically.</p>
      {importError && <p role="alert" className="text-destructive">{importError}</p>}
      <p className="text-muted-foreground">Queries and parameters are logged. Shared query links include these values.</p>
      <DialogFooter><Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button><Button disabled={errors.some(Boolean)} onClick={() => { onChange(JSON.stringify(rows.map(parameterValue))); setOpen(false) }}>Apply settings</Button></DialogFooter>
    </DialogContent>
  </Dialog>
}
