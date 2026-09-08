import { Button } from "@/components/ui/button"
import type { DatasetBrief } from "@/types/answer"

export function DatasetSpecification({ draft, busy, onEdit }: { draft: DatasetBrief; busy: boolean; onEdit: () => void }) {
  return <div className="dataset-definition-card flex flex-col gap-4">
    <div className="flex items-center justify-between gap-2"><h2>Your dataset</h2><Button variant="ghost" size="sm" disabled={busy} onClick={onEdit}>Edit</Button></div>
    <div><strong>{draft.title}</strong><p>{draft.grain}</p></div>
    <ul aria-label="Dataset columns">{draft.fields.map(field => <li key={field.name}><strong>{field.name}</strong><p>{field.meaning}</p></li>)}</ul>
    <div><strong>Sources & scope</strong><p>{draft.population}</p></div>
  </div>
}
