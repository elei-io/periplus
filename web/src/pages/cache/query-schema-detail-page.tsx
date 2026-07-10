import {
  ArrowLeftIcon,
  CheckCircle2Icon,
  ExternalLinkIcon,
  ListFilterIcon,
  RefreshCwIcon,
  SaveIcon,
  Trash2Icon,
  XCircleIcon,
} from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  useDeleteQuerySchema,
  useQuerySchema,
  useUpdateQuerySchema,
} from "@/hooks/use-resource-data"

type QuerySchemaDetailPageProps = {
  schemaId: string
}

export function QuerySchemaDetailPage({
  schemaId,
}: QuerySchemaDetailPageProps) {
  const schemaQuery = useQuerySchema(schemaId)
  const schema = schemaQuery.data

  if (schemaQuery.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        Loading query schema...
      </div>
    )
  }

  if (!schema) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        Query schema not found.
      </div>
    )
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="flex flex-col gap-3 border-b pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <Button
              variant="ghost"
              size="icon-sm"
              nativeButton={false}
              render={<a href="/cache/query-schemas" />}
            >
              <ArrowLeftIcon />
            </Button>
            <div className="grid min-w-0 gap-1">
              <div className="flex min-w-0 items-center gap-2">
                <ListFilterIcon className="size-4 shrink-0 text-muted-foreground" />
                <h1 className="truncate text-lg font-medium">{schema.match}</h1>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge variant={schema.enabled ? "secondary" : "destructive"}>
                  {schema.enabled ? <CheckCircle2Icon /> : <XCircleIcon />}
                  {schema.enabled ? "Enabled" : "Disabled"}
                </Badge>
                <Badge variant="outline">{schema.schema_type}</Badge>
                <Badge variant="outline">{schema.param_count} params</Badge>
                <Badge variant="outline">
                  {schema.evidence_count} evidence
                </Badge>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={schemaQuery.isFetching}
              onClick={() => void schemaQuery.refetch()}
            >
              <RefreshCwIcon />
              Refresh
            </Button>
            <Button
              variant="outline"
              size="sm"
              nativeButton={false}
              render={
                <a href={schema.match} target="_blank" rel="noreferrer" />
              }
            >
              <ExternalLinkIcon />
              Open
            </Button>
          </div>
        </div>
      </section>

      <div className="grid min-h-0 gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
        <JsonCard title="Extraction schema" value={schema.extraction_schema} />
        <JsonCard title="Params" value={schema.params_json} />
        <JsonCard title="Evidence" value={schema.evidence_json} />
        <div className="grid gap-4">
          <QuerySchemaAdmin key={schema.id} schema={schema} />
          <MetadataCard schema={schema} />
          <JsonCard title="Inputs" value={schema.inputs_json} />
          <JsonCard title="Warnings" value={schema.warnings_json} />
        </div>
      </div>
    </div>
  )
}

function QuerySchemaAdmin({
  schema,
}: {
  schema: NonNullable<ReturnType<typeof useQuerySchema>["data"]>
}) {
  const updateSchema = useUpdateQuerySchema(schema.id)
  const deleteSchema = useDeleteQuerySchema(schema.id)
  const [enabled, setEnabled] = useState(schema.enabled)
  const [priority, setPriority] = useState(String(schema.priority))

  const save = () => {
    const parsedPriority = Number(priority)
    if (!Number.isInteger(parsedPriority)) {
      toast.error("Priority must be an integer.")
      return
    }

    updateSchema.mutate({
      enabled,
      priority: parsedPriority,
    })
  }

  const deleteCurrentSchema = () => {
    if (
      !window.confirm(
        "Delete this query schema? Atlas will generate a fresh one the next time this page shape is extracted."
      )
    ) {
      return
    }

    deleteSchema.mutate(undefined, {
      onSuccess: () => {
        window.history.pushState(null, "", "/cache/query-schemas")
        window.dispatchEvent(new PopStateEvent("popstate"))
      },
    })
  }

  return (
    <Card size="sm">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Admin</CardTitle>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="destructive"
              onClick={deleteCurrentSchema}
              disabled={deleteSchema.isPending}
            >
              <Trash2Icon />
              Delete
            </Button>
            <Button size="sm" onClick={save} disabled={updateSchema.isPending}>
              <SaveIcon />
              Save
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_8rem]">
        <div className="grid gap-1">
          <Label>Enabled</Label>
          <div className="flex h-9 items-center gap-2">
            <Switch checked={enabled} onCheckedChange={setEnabled} />
            <span className="text-xs text-muted-foreground">
              {enabled ? "On" : "Off"}
            </span>
          </div>
        </div>
        <div className="grid gap-1">
          <Label>Priority</Label>
          <Input
            type="number"
            step={1}
            value={priority}
            onChange={(event) => setPriority(event.target.value)}
          />
        </div>
      </CardContent>
    </Card>
  )
}

function MetadataCard({
  schema,
}: {
  schema: NonNullable<ReturnType<typeof useQuerySchema>["data"]>
}) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Metadata</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-2 text-sm">
        <MetaRow label="ID" value={schema.id} mono />
        <MetaRow label="URL match" value={schema.url_match_id ?? "-"} mono />
        <MetaRow label="Identity" value={schema.identity_key} mono />
        <MetaRow label="Hash" value={schema.schema_hash} mono />
        <MetaRow label="Domain" value={schema.domain ?? "-"} />
        <MetaRow label="Path" value={schema.path ?? "-"} />
        <MetaRow
          label="Crawl"
          value={schema.generated_from_crawl_id ?? "-"}
          mono
        />
        <MetaRow
          label="Document"
          value={schema.generated_from_document_id ?? "-"}
          mono
        />
        <MetaRow
          label="Task run"
          value={schema.generated_by_task_run_id ?? "-"}
          mono
        />
        <MetaRow label="Created" value={formatDate(schema.created_at)} />
        <MetaRow label="Updated" value={formatDate(schema.updated_at)} />
      </CardContent>
    </Card>
  )
}

function MetaRow({
  label,
  value,
  mono = false,
}: {
  label: string
  value: string
  mono?: boolean
}) {
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className={`truncate ${mono ? "font-mono text-xs" : ""}`}>
        {value}
      </span>
    </div>
  )
}

function JsonCard({ title, value }: { title: string; value: unknown }) {
  return (
    <Card size="sm" className="min-h-0">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <pre className="max-h-[32rem] overflow-auto rounded-md border bg-muted/30 p-3 text-xs">
          {JSON.stringify(value, null, 2)}
        </pre>
      </CardContent>
    </Card>
  )
}

function formatDate(value: string | null) {
  if (!value) {
    return "-"
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))
}
