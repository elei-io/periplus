"use client"

import { useRef, useState, Fragment } from "react"
import { Plus, Trash2, ArrowUp, ArrowDown, Download, Upload, MoreHorizontal, Table2 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import type { AnalysisQueryResult } from "@/types/analysis"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { datasetDraftSchema, type DatasetBrief } from "@/types/answer"
import { extractApiError } from "@/lib/api"

export const emptyDatasetBrief: DatasetBrief = { title: "", grain: "", population: "", fields: [] }

export function DatasetSpecification({ draft, busy, onChange, preview }: { draft: DatasetBrief; busy: boolean; onChange: (value: DatasetBrief) => void; preview?: AnalysisQueryResult }) {
  const [editing, setEditing] = useState<number | null>(null)
  const [started, setStarted] = useState(false)
  const empty = !started && !draft.title && !draft.fields.length
  const fileInput = useRef<HTMLInputElement>(null)
  function field(index: number, patch: Partial<DatasetBrief["fields"][number]>) {
    onChange({ ...draft, fields: draft.fields.map((value, i) => i === index ? { ...value, ...patch } : value) })
  }
  function move(index: number, direction: number) {
    const fields = [...draft.fields]
    ;[fields[index], fields[index + direction]] = [fields[index + direction], fields[index]]
    onChange({ ...draft, fields })
    setEditing(index + direction)
  }
  function saveDraft() {
    try {
      const url = URL.createObjectURL(new Blob([JSON.stringify(draft, null, 2)], { type: "application/json" }))
      const link = document.createElement("a")
      link.href = url; link.download = "periplus-draft.json"; link.click(); URL.revokeObjectURL(url)
    } catch (error) { toast.error(extractApiError(error)) }
  }
  async function importDefinition(file?: File) {
    if (!file) return
    try {
      if (file.size > 64000) throw new Error("Use a JSON definition smaller than 64 KB.")
      const value = JSON.parse(await file.text())
      const parsed = datasetDraftSchema.parse(value.brief ?? value)
      onChange(parsed)
      setStarted(true)
      toast.success("Schema imported. Complete any unfinished fields before validating.")
    } catch (error) { toast.error(extractApiError(error)) }
    finally { if (fileInput.current) fileInput.current.value = "" }
  }
  return <div className="schema-editor flex flex-col gap-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><p className="schema-caption">{draft.fields.length ? `${draft.fields.length} ${draft.fields.length === 1 ? "column" : "columns"} · exact types and nullability` : "Start with your requirements"}</p><Button variant="ghost" size="sm" disabled={busy || empty} onClick={saveDraft}><Download />Save draft</Button><Button variant="outline" size="sm" disabled={busy} onClick={() => fileInput.current?.click()}><Upload />Import schema</Button><input ref={fileInput} type="file" accept=".json,application/json" hidden onChange={event => void importDefinition(event.target.files?.[0])} /></div>
    {empty ? <div className="builder-empty"><Table2 /><h2>Your dataset, your structure</h2><p>Describe what you need in the conversation, import a schema, or define your columns here.</p><Button variant="outline" disabled={busy} onClick={() => setStarted(true)}><Plus />Define columns</Button></div> : <>
    <div className="grid gap-4">
      <label className="flex flex-col gap-2">Dataset name<Input className="schema-title" value={draft.title} disabled={busy} maxLength={120} onChange={event => onChange({ ...draft, title: event.target.value })} placeholder="Name your dataset" /></label>
      <label className="flex flex-col gap-2">One row represents<Input value={draft.grain} disabled={busy} maxLength={300} onChange={event => onChange({ ...draft, grain: event.target.value })} placeholder="Describe what makes one row" /></label>
    </div>
    <Table aria-label="Required dataset schema"><TableHeader><TableRow><TableHead>Column name</TableHead><TableHead>Data type</TableHead><TableHead>Required</TableHead><TableHead><span className="sr-only">Column options</span></TableHead></TableRow></TableHeader><TableBody>{draft.fields.map((value, index) => {
      const source = preview?.columns.indexOf(value.name) ?? -1
      const issue = !preview ? undefined : source < 0 ? "Missing from preview" : preview.types[source] !== value.type ? `Preview type: ${preview.types[source]}` : !value.nullable && preview.rows.some(row => row[source] == null) ? "Preview contains missing values" : undefined
      return <Fragment key={index}><TableRow>
      <TableCell><div className="flex min-w-40 flex-col gap-2"><Input aria-label={`Column ${index + 1} name`} disabled={busy} value={value.name} maxLength={128} onChange={event => field(index, { name: event.target.value })} />{issue && <span className="schema-issue">{issue}</span>}</div></TableCell>
      <TableCell className="min-w-36"><Input aria-label={`Column ${index + 1} type`} disabled={busy} value={value.type} maxLength={60} placeholder="DuckDB data type" onChange={event => field(index, { type: event.target.value })} /></TableCell>
      <TableCell><Switch aria-label={`Column ${index + 1} required`} disabled={busy} checked={!value.nullable} onCheckedChange={checked => field(index, { nullable: !checked })} /></TableCell>
      <TableCell><Button variant="ghost" size="icon" aria-label={`Edit column ${index + 1} details`} aria-expanded={editing === index} onClick={() => setEditing(editing === index ? null : index)}><MoreHorizontal /></Button></TableCell>
    </TableRow>{editing === index && <TableRow><TableCell colSpan={4}><div className="column-details">
      <label>Column description<Input aria-label={`Column ${index + 1} meaning`} disabled={busy} value={value.meaning} maxLength={160} placeholder="What does this column represent?" onChange={event => field(index, { meaning: event.target.value })} /></label>
      <div className="flex gap-1"><Button variant="ghost" size="icon" aria-label={`Move column ${index + 1} up`} disabled={busy || index === 0} onClick={() => move(index, -1)}><ArrowUp /></Button><Button variant="ghost" size="icon" aria-label={`Move column ${index + 1} down`} disabled={busy || index === draft.fields.length - 1} onClick={() => move(index, 1)}><ArrowDown /></Button><Button variant="ghost" size="icon" aria-label={`Remove column ${index + 1}`} disabled={busy} onClick={() => onChange({ ...draft, fields: draft.fields.filter((_, i) => i !== index) })}><Trash2 /></Button></div></div></TableCell></TableRow>}</Fragment>})}</TableBody></Table>
    <Button className="self-start" variant="outline" size="sm" disabled={busy || draft.fields.length >= 64} onClick={() => onChange({ ...draft, fields: [...draft.fields, { name: "", type: "VARCHAR", nullable: true, meaning: "" }] })}><Plus />Add column</Button>
    <label className="flex flex-col gap-2">Sources and selection rules<Textarea disabled={busy} value={draft.population} maxLength={500} onChange={event => onChange({ ...draft, population: event.target.value })} placeholder="Define which sources and records to include" /></label>
    </>}
    <details className="workspace-disclosure"><summary>JSON definition format</summary><p>Import a saved Periplus definition or a JSON object with title, grain, population, and fields. Each field has name, type, meaning, and nullable. All types and nullability are explicit; example rows alone do not establish a contract.</p><pre className="overflow-auto">{JSON.stringify({ title: "Dataset name", grain: "What one row represents", population: "Sources and selection rules", fields: [{ name: "column_name", type: "VARCHAR", meaning: "What this column represents", nullable: true }] }, null, 2)}</pre></details>
  </div>
}
