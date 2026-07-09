import {
  ArrowLeftIcon,
  FileArchiveIcon,
  RouteIcon,
  SaveIcon,
  ShieldAlertIcon,
} from "lucide-react"
import { useState, type ReactNode } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  usePaginationSchema,
  useUpdatePaginationSchema,
} from "@/hooks/use-history-data"
import type { PaginationSchemaRecord } from "@/types/history"

export function PaginationSchemaDetailPage({ schemaId }: { schemaId: string }) {
  const schemaQuery = usePaginationSchema(schemaId)
  const schema = schemaQuery.data

  if (schemaQuery.isLoading) {
    return <CenteredText>Loading pagination schema...</CenteredText>
  }

  if (!schema) {
    return (
      <div className="flex min-h-0 w-full flex-col gap-3">
        <BackButton />
        <div className="rounded-md border bg-card/80 p-4 text-sm text-muted-foreground">
          Pagination schema not found.
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto">
      <section className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
        <div className="flex min-w-0 items-center gap-2">
          <RouteIcon className="size-4 text-muted-foreground" />
          <h1 className="truncate text-lg font-medium">Pagination Schema</h1>
          <Badge variant="outline">query</Badge>
          <Badge variant={schema.enabled ? "secondary" : "destructive"}>
            {schema.enabled ? "Enabled" : "Disabled"}
          </Badge>
        </div>
        <BackButton />
      </section>

      <section className="grid gap-3 lg:grid-cols-2">
        <DetailGroup
          title="Identity"
          items={[
            ["Schema ID", schema.id],
            ["Identity Key", schema.identity_key],
            ["Match", schema.match],
            ["Priority", String(schema.priority)],
          ]}
        />
        <DetailGroup
          title="Scope"
          items={[
            ["Domain", schema.domain],
            ["Path", schema.path],
            ["Validation", schema.validation_status],
            ["Created", formatDate(schema.created_at)],
            ["Updated", formatDate(schema.updated_at)],
          ]}
        />
        <DetailGroup
          title="Selectors"
          items={[
            ["Item Selector", schema.item_selector],
            ["Next Selector", schema.next_button_selector],
            ["Expected Max Items", formatNullableNumber(schema.expected_max_item_count)],
          ]}
        />
        <DetailGroup
          title="Query Pagination"
          items={[
            ["Param Key", schema.query_param_key],
            ["Value Template", schema.query_param_value_template],
            ["Start Value", String(schema.start_value)],
            ["Value Step", String(schema.value_step)],
          ]}
        />
      </section>

      <SchemaAdminEditor key={schema.id} schema={schema} />

      <LinkActions schema={schema} />

      <section className="grid gap-3 lg:grid-cols-2">
        <DetailGroup
          title="Failure State"
          items={[
            ["Failure Count", String(schema.failure_count)],
            ["Last Failed At", formatDate(schema.last_failed_at)],
            ["Last Error", schema.last_error],
          ]}
        />
        <WarningDetails schema={schema} />
      </section>

      <section className="grid gap-3 xl:grid-cols-2">
        <JsonPanel title="Inputs" value={schema.inputs_json} />
        <JsonPanel title="Warning Metadata" value={schema.warnings_json} compact />
      </section>
    </div>
  )
}

function SchemaAdminEditor({ schema }: { schema: PaginationSchemaRecord }) {
  const updateSchema = useUpdatePaginationSchema(schema.id)
  const [match, setMatch] = useState(schema.match)
  const [enabled, setEnabled] = useState(schema.enabled)
  const [priority, setPriority] = useState(String(schema.priority))
  const [validationStatus, setValidationStatus] = useState(schema.validation_status ?? "")
  const [itemSelector, setItemSelector] = useState(schema.item_selector)
  const [nextButtonSelector, setNextButtonSelector] = useState(schema.next_button_selector ?? "")
  const [expectedMaxItemCount, setExpectedMaxItemCount] = useState(
    schema.expected_max_item_count === null ? "" : String(schema.expected_max_item_count)
  )
  const [queryParamKey, setQueryParamKey] = useState(schema.query_param_key)
  const [queryParamValueTemplate, setQueryParamValueTemplate] = useState(
    schema.query_param_value_template
  )
  const [startValue, setStartValue] = useState(String(schema.start_value))
  const [valueStep, setValueStep] = useState(String(schema.value_step))

  const save = () => {
    const parsedPriority = parseInteger(priority, "Priority")
    const parsedExpectedMaxItemCount = parseOptionalInteger(
      expectedMaxItemCount,
      "Expected max item count"
    )
    const parsedStartValue = parseInteger(startValue, "Start value")
    const parsedValueStep = parseInteger(valueStep, "Value step")

    if (
      parsedPriority === null ||
      parsedExpectedMaxItemCount === undefined ||
      parsedStartValue === null ||
      parsedValueStep === null
    ) {
      return
    }

    if (parsedValueStep < 1) {
      toast.error("Value step must be at least 1.")
      return
    }

    updateSchema.mutate({
      match,
      enabled,
      priority: parsedPriority,
      validation_status: validationStatus.trim() || null,
      item_selector: itemSelector,
      next_button_selector: nextButtonSelector.trim() || null,
      expected_max_item_count: parsedExpectedMaxItemCount,
      query_param_key: queryParamKey,
      query_param_value_template: queryParamValueTemplate,
      start_value: parsedStartValue,
      value_step: parsedValueStep,
    })
  }

  return (
    <section className="rounded-md border bg-card/80 p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium">Admin</h2>
        <Button size="sm" onClick={save} disabled={updateSchema.isPending}>
          <SaveIcon />
          Save
        </Button>
      </div>
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_8rem_10rem]">
        <Field label="Match">
          <Input value={match} onChange={(event) => setMatch(event.target.value)} />
        </Field>
        <Field label="Priority">
          <Input
            type="number"
            step={1}
            value={priority}
            onChange={(event) => setPriority(event.target.value)}
          />
        </Field>
        <Field label="Enabled">
          <div className="flex h-7 items-center gap-2">
            <Switch checked={enabled} onCheckedChange={setEnabled} />
            <span className="text-xs text-muted-foreground">{enabled ? "On" : "Off"}</span>
          </div>
        </Field>
      </div>
      <div className="mt-3 grid gap-3 xl:grid-cols-2">
        <Field label="Item Selector">
          <Input
            value={itemSelector}
            onChange={(event) => setItemSelector(event.target.value)}
          />
        </Field>
        <Field label="Next Button Selector">
          <Input
            value={nextButtonSelector}
            onChange={(event) => setNextButtonSelector(event.target.value)}
          />
        </Field>
      </div>
      <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_8rem_8rem_8rem]">
        <Field label="Query Param Key">
          <Input
            value={queryParamKey}
            onChange={(event) => setQueryParamKey(event.target.value)}
          />
        </Field>
        <Field label="Value Template">
          <Input
            value={queryParamValueTemplate}
            onChange={(event) => setQueryParamValueTemplate(event.target.value)}
          />
        </Field>
        <Field label="Start">
          <Input
            type="number"
            step={1}
            value={startValue}
            onChange={(event) => setStartValue(event.target.value)}
          />
        </Field>
        <Field label="Step">
          <Input
            type="number"
            step={1}
            min={1}
            value={valueStep}
            onChange={(event) => setValueStep(event.target.value)}
          />
        </Field>
        <Field label="Max Items">
          <Input
            type="number"
            step={1}
            min={0}
            value={expectedMaxItemCount}
            onChange={(event) => setExpectedMaxItemCount(event.target.value)}
          />
        </Field>
      </div>
      <div className="mt-3 max-w-sm">
        <Field label="Validation">
          <Input
            value={validationStatus}
            onChange={(event) => setValidationStatus(event.target.value)}
          />
        </Field>
      </div>
    </section>
  )
}

function Field({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="grid gap-1">
      <Label>{label}</Label>
      {children}
    </div>
  )
}

function LinkActions({ schema }: { schema: PaginationSchemaRecord }) {
  return (
    <section className="flex flex-wrap gap-2">
      {schema.generated_from_crawl_id ? (
        <Button
          variant="outline"
          size="sm"
          nativeButton={false}
          render={<a href={`/history/crawls/${schema.generated_from_crawl_id}`} />}
        >
          <RouteIcon />
          Source Crawl
        </Button>
      ) : null}
      {schema.generated_from_artifact_id ? (
        <Button
          variant="outline"
          size="sm"
          nativeButton={false}
          render={<a href={`/history/artifacts/${schema.generated_from_artifact_id}`} />}
        >
          <FileArchiveIcon />
          Source Artifact
        </Button>
      ) : null}
    </section>
  )
}

function BackButton() {
  return (
    <Button
      variant="outline"
      size="sm"
      nativeButton={false}
      render={<a href="/history/pagination-schemas" />}
    >
      <ArrowLeftIcon />
      Pagination Schemas
    </Button>
  )
}

function WarningDetails({ schema }: { schema: PaginationSchemaRecord }) {
  const warnings = Array.isArray(schema.warnings_json.warnings)
    ? schema.warnings_json.warnings
    : []

  return (
    <section className="rounded-md border bg-card/80 p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ShieldAlertIcon className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">Warnings</h2>
        </div>
        <Badge variant={warnings.length > 0 ? "destructive" : "outline"}>
          {warnings.length} total
        </Badge>
      </div>
      {warnings.length > 0 ? (
        <pre className="max-h-40 overflow-auto rounded-md bg-muted/40 p-3 text-xs">
          {JSON.stringify(warnings, null, 2)}
        </pre>
      ) : (
        <div className="text-xs text-muted-foreground">No warning details.</div>
      )}
    </section>
  )
}

function DetailGroup({
  title,
  items,
}: {
  title: string
  items: Array<[string, string | null]>
}) {
  return (
    <section className="rounded-md border bg-card/80 p-3">
      <h2 className="mb-2 text-sm font-medium">{title}</h2>
      <dl className="grid gap-2 text-xs">
        {items.map(([label, value]) => (
          <div key={label} className="grid gap-1 md:grid-cols-[9rem_minmax(0,1fr)]">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="min-w-0 break-words font-mono">{value || "-"}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function JsonPanel({
  title,
  value,
  compact = false,
}: {
  title: string
  value: unknown
  compact?: boolean
}) {
  return (
    <section className="min-w-0 rounded-md border bg-card/80 p-3">
      <h2 className="mb-2 text-sm font-medium">{title}</h2>
      <pre
        className={
          compact
            ? "max-h-64 overflow-auto rounded-md bg-muted/40 p-3 text-xs"
            : "max-h-[34rem] overflow-auto rounded-md bg-muted/40 p-3 text-xs"
        }
      >
        {JSON.stringify(value, null, 2)}
      </pre>
    </section>
  )
}

function CenteredText({ children }: { children: string }) {
  return (
    <div className="flex min-h-0 w-full items-center justify-center text-sm text-muted-foreground">
      {children}
    </div>
  )
}

function parseInteger(value: string, label: string) {
  const parsed = Number(value)
  if (!Number.isInteger(parsed)) {
    toast.error(`${label} must be an integer.`)
    return null
  }
  return parsed
}

function parseOptionalInteger(value: string, label: string) {
  if (!value.trim()) {
    return null
  }

  const parsed = Number(value)
  if (!Number.isInteger(parsed)) {
    toast.error(`${label} must be an integer.`)
    return undefined
  }
  return parsed
}

function formatNullableNumber(value: number | null) {
  return value === null ? null : String(value)
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
