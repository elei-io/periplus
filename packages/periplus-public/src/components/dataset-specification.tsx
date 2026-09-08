"use client"

import { toast } from "sonner"
import { Plus, Trash2, ArrowRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { datasetBriefSchema, type DatasetBrief } from "@/types/answer"

export function DatasetSpecification({ draft, onChange, busy, onBuild }: { draft: DatasetBrief; onChange: (brief: DatasetBrief) => void; busy: boolean; onBuild: (brief: DatasetBrief) => void }) {
  const update = (key: keyof DatasetBrief, value: unknown) => onChange({ ...draft, [key]: value })
  return <Card className="dataset-definition-card">
    <CardHeader><CardTitle>Your dataset</CardTitle><CardDescription>The definition we’re building together.</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-4">
      <div><strong>{draft.title}</strong><p>{draft.grain}</p></div>
      <ul className="flex flex-col gap-3" aria-label="Dataset columns">{draft.fields.map((field, index) => <li key={index}><div className="flex flex-wrap items-center justify-between gap-2"><strong>{field.name}</strong><span>{field.type}</span></div><p>{field.meaning}</p><small>{field.nullable ? "Missing values allowed" : "Required"}</small></li>)}</ul>
      <details><summary>Scope & rules</summary><dl className="flex flex-col gap-3 py-3">{([["Sources", draft.population], ["Observations", draft.time_scope], ["Acceptance", draft.acceptance]] as const).map(([label, value]) => <div key={label}><dt><strong>{label}</strong></dt><dd>{value}</dd></div>)}</dl></details>
      <details><summary>Edit definition</summary><form className="flex flex-col gap-4" onSubmit={event => {
      event.preventDefault()
      const parsed = datasetBriefSchema.safeParse(draft)
      if (!parsed.success) { toast.error(parsed.error.issues[0]?.message ?? "Check the dataset definition."); return }
      if (new Set(draft.fields.map(field => field.name)).size !== draft.fields.length) { toast.error("Column names must be unique."); return }
      onBuild(parsed.data)
    }}>
      <fieldset disabled={busy} className="flex flex-col gap-4">
        <label className="flex flex-col gap-2">Dataset name<Input value={draft.title} maxLength={120} required onChange={event => update("title", event.target.value)} /></label>
        <div className="grid gap-4">{([
          ["purpose", "Intended use"], ["grain", "One row represents"], ["population", "Sources & population"],
          ["time_scope", "Observation selection"], ["acceptance", "Duplicates, missing values & acceptance rules"],
        ] as const).map(([key, label]) => <label key={key} className="flex flex-col gap-2">{label}<Textarea value={draft[key]} maxLength={600} required rows={2} onChange={event => update(key, event.target.value)} /></label>)}</div>
        <div className="flex flex-col gap-3"><strong>Output columns · in order</strong>{draft.fields.map((field, index) => <div key={index} className="grid items-end gap-2">
          <label className="flex flex-col gap-2">Name<Input aria-label={`Column ${index + 1} name`} value={field.name} required maxLength={60} onChange={event => update("fields", draft.fields.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} /></label>
          <label className="flex flex-col gap-2">SQL type<Input aria-label={`Column ${index + 1} type`} value={field.type} required maxLength={60} onChange={event => update("fields", draft.fields.map((item, i) => i === index ? { ...item, type: event.target.value } : item))} /></label>
          <label className="flex flex-col gap-2">Meaning<Input aria-label={`Column ${index + 1} meaning`} title={field.meaning} value={field.meaning} required maxLength={240} onChange={event => update("fields", draft.fields.map((item, i) => i === index ? { ...item, meaning: event.target.value } : item))} /></label>
          <div className="flex items-center gap-2"><Button type="button" variant="outline" aria-pressed={field.nullable} onClick={() => update("fields", draft.fields.map((item, i) => i === index ? { ...item, nullable: !item.nullable } : item))}>{field.nullable ? "Missing allowed" : "Required value"}</Button><Button type="button" variant="ghost" size="icon" aria-label={`Remove column ${index + 1}`} disabled={draft.fields.length === 1} onClick={() => update("fields", draft.fields.filter((_, i) => i !== index))}><Trash2 /></Button></div>
        </div>)}<Button type="button" variant="outline" className="self-start" disabled={draft.fields.length >= 16} onClick={() => update("fields", [...draft.fields, { name: `field_${draft.fields.length + 1}`, type: "VARCHAR", meaning: "", nullable: true }])}><Plus />Add column</Button></div>
        <Button type="submit" className="self-start">Apply changes & rebuild<ArrowRight /></Button>
      </fieldset>
    </form></details></CardContent>
  </Card>
}
