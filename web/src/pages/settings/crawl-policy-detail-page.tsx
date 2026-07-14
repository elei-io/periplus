import {
  ArrowLeftIcon,
  CheckCircle2Icon,
  RefreshCwIcon,
  SaveIcon,
  ShieldCheckIcon,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import {
  useCrawlPolicy,
  useDeleteCrawlPolicy,
  useUpdateCrawlPolicy,
} from "@/hooks/use-resource-data"
import type { CrawlPolicyDetailRecord } from "@/types/resources"

type CrawlPolicyDetailPageProps = {
  policyId: string
}

type CrawlPolicyTemplate =
  | "static_fast"
  | "static_stable"
  | "dynamic_scan"
  | "dynamic_stable"
  | "app_stable"
  | "app_deep"

type CrawlPolicyTemplateConfig = {
  template: CrawlPolicyTemplate
  label: string
  mode: "static" | "dynamic" | "app"
  wait: "none" | "stable"
  maxConcurrency: number
  runConfigOverrides: Record<string, unknown>
}

const crawlPolicyTemplates: CrawlPolicyTemplateConfig[] = [
  {
    template: "static_fast",
    label: "Static fast",
    mode: "static",
    wait: "none",
    maxConcurrency: 10,
    runConfigOverrides: {},
  },
  {
    template: "static_stable",
    label: "Static stable",
    mode: "static",
    wait: "stable",
    maxConcurrency: 10,
    runConfigOverrides: {},
  },
  {
    template: "dynamic_scan",
    label: "Dynamic scan",
    mode: "dynamic",
    wait: "none",
    maxConcurrency: 10,
    runConfigOverrides: {},
  },
  {
    template: "dynamic_stable",
    label: "Dynamic stable",
    mode: "dynamic",
    wait: "stable",
    maxConcurrency: 10,
    runConfigOverrides: {},
  },
  {
    template: "app_stable",
    label: "App stable",
    mode: "app",
    wait: "stable",
    maxConcurrency: 5,
    runConfigOverrides: {},
  },
  {
    template: "app_deep",
    label: "App deep",
    mode: "app",
    wait: "stable",
    maxConcurrency: 5,
    runConfigOverrides: {
      delay_before_return_html: 6.0,
      max_scroll_steps: 8,
      scroll_delay: 1.0,
    },
  },
]

function templateConfigFor(value: unknown) {
  return crawlPolicyTemplates.find((template) => template.template === value)
}

export function CrawlPolicyDetailPage({
  policyId,
}: CrawlPolicyDetailPageProps) {
  const policyQuery = useCrawlPolicy(policyId)
  const policy = policyQuery.data

  if (policyQuery.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        Loading crawl policy...
      </div>
    )
  }

  if (!policy) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        Crawl policy not found.
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
              render={<a href="/crawl-policies" />}
            >
              <ArrowLeftIcon />
            </Button>
            <div className="grid min-w-0 gap-1">
              <div className="flex min-w-0 items-center gap-2">
                <ShieldCheckIcon className="size-4 shrink-0 text-muted-foreground" />
                <h1 className="truncate text-lg font-medium">{policy.match}</h1>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge variant={policy.enabled ? "secondary" : "destructive"}>
                  {policy.enabled ? <CheckCircle2Icon /> : <XCircleIcon />}
                  {policy.enabled ? "Enabled" : "Disabled"}
                </Badge>
                <Badge variant="outline">
                  {String(policy.config.profile ?? "http")}
                </Badge>
                <Badge variant="outline">
                  {String(objectValue(policy.config.config).mode ?? "")}
                </Badge>
              </div>
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={policyQuery.isFetching}
            onClick={() => void policyQuery.refetch()}
          >
            <RefreshCwIcon />
            Refresh
          </Button>
        </div>
      </section>

      <div className="grid min-h-0 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(22rem,0.45fr)]">
        <ConfigEditor key={policy.id} policy={policy} />
        <div className="grid content-start gap-4">
          <TemplateCard policy={policy} />
          <AdminCard policy={policy} />
          <MetadataCard policy={policy} />
        </div>
      </div>
    </div>
  )
}

function ConfigEditor({ policy }: { policy: CrawlPolicyDetailRecord }) {
  const updatePolicy = useUpdateCrawlPolicy(policy.id)
  const [configText, setConfigText] = useState(
    JSON.stringify(policy.config, null, 2)
  )

  const save = () => {
    try {
      const parsed = JSON.parse(configText) as unknown
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        toast.error("Config must be a JSON object.")
        return
      }
      updatePolicy.mutate({ config: parsed as Record<string, unknown> })
    } catch {
      toast.error("Config must be valid JSON.")
    }
  }

  return (
    <Card size="sm" className="min-h-0">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Config</CardTitle>
          <Button size="sm" onClick={save} disabled={updatePolicy.isPending}>
            <SaveIcon />
            Save config
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <Textarea
          className="min-h-[34rem] resize-y font-mono text-xs"
          value={configText}
          spellCheck={false}
          onChange={(event) => setConfigText(event.target.value)}
        />
      </CardContent>
    </Card>
  )
}

function TemplateCard({ policy }: { policy: CrawlPolicyDetailRecord }) {
  const updatePolicy = useUpdateCrawlPolicy(policy.id)
  const profileConfig = objectValue(policy.config.config)
  const currentTemplate =
    templateConfigFor(profileConfig.template) ?? crawlPolicyTemplates[0]
  const initialConcurrency =
    typeof policy.config.concurrency === "number"
      ? policy.config.concurrency
      : currentTemplate.maxConcurrency
  const [templateName, setTemplateName] = useState<CrawlPolicyTemplate>(
    currentTemplate.template
  )
  const selectedTemplate = templateConfigFor(templateName) ?? currentTemplate
  const [maxConcurrency, setMaxConcurrency] = useState(
    String(initialConcurrency)
  )

  const saveTemplate = () => {
    const parsedConcurrency = Number.parseInt(maxConcurrency, 10)
    if (!Number.isFinite(parsedConcurrency) || parsedConcurrency < 1) {
      toast.error("Concurrency must be at least 1.")
      return
    }

    updatePolicy.mutate({
      config: {
        profile: "browser",
        concurrency: parsedConcurrency,
        config: {
          ...profileConfig,
          template: selectedTemplate.template,
          mode: selectedTemplate.mode,
          wait: selectedTemplate.wait,
          run_config_overrides: selectedTemplate.runConfigOverrides,
          cache_block_rules: profileConfig.cache_block_rules ?? {
            quality_flag_codes: [],
          },
          selection: {
            ...objectValue(profileConfig.selection),
            reason: "admin selected template",
          },
        },
      },
    })
  }

  return (
    <Card size="sm">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Browser template</CardTitle>
          <Button
            size="sm"
            onClick={saveTemplate}
            disabled={updatePolicy.isPending}
          >
            <SaveIcon />
            Save template
          </Button>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="grid gap-1">
          <Label>Template</Label>
          <Select
            value={templateName}
            onValueChange={(value) => {
              const nextTemplate = templateConfigFor(value)
              if (!nextTemplate) {
                return
              }
              setTemplateName(nextTemplate.template)
              setMaxConcurrency(String(nextTemplate.maxConcurrency))
            }}
          >
            <SelectTrigger
              aria-label="Crawl policy template"
              className="w-full"
            >
              <span>{selectedTemplate.label}</span>
            </SelectTrigger>
            <SelectContent>
              {crawlPolicyTemplates.map((template) => (
                <SelectItem key={template.template} value={template.template}>
                  {template.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-1">
          <Label>Max concurrency</Label>
          <Input
            type="number"
            min={1}
            step={1}
            value={maxConcurrency}
            onChange={(event) => setMaxConcurrency(event.target.value)}
          />
        </div>
        <div className="grid gap-2 rounded-md border bg-muted/20 p-3 text-sm">
          <MetaRow label="Mode" value={selectedTemplate.mode} />
          <MetaRow label="Wait" value={selectedTemplate.wait} />
          <MetaRow
            label="Overrides"
            value={formatOverrides(selectedTemplate.runConfigOverrides)}
          />
        </div>
      </CardContent>
    </Card>
  )
}

function AdminCard({ policy }: { policy: CrawlPolicyDetailRecord }) {
  const updatePolicy = useUpdateCrawlPolicy(policy.id)
  const deletePolicy = useDeleteCrawlPolicy(policy.id)
  const [enabled, setEnabled] = useState(policy.enabled)

  const saveEnabled = () => {
    updatePolicy.mutate({ enabled })
  }

  const invalidate = () => {
    setEnabled(false)
    updatePolicy.mutate({ enabled: false })
  }

  const deleteCurrentPolicy = () => {
    if (
      !window.confirm(
        "Delete this crawl policy? Matching crawls will use the default HTTP profile until you create a new policy."
      )
    ) {
      return
    }

    deletePolicy.mutate(undefined, {
      onSuccess: () => {
        window.history.pushState(null, "", "/crawl-policies")
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
              onClick={deleteCurrentPolicy}
              disabled={deletePolicy.isPending}
            >
              <Trash2Icon />
              Delete
            </Button>
            <Button
              size="sm"
              onClick={saveEnabled}
              disabled={updatePolicy.isPending}
            >
              <SaveIcon />
              Save
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="grid gap-1">
          <Label>Enabled</Label>
          <div className="flex h-9 items-center gap-2">
            <Switch checked={enabled} onCheckedChange={setEnabled} />
            <span className="text-xs text-muted-foreground">
              {enabled ? "On" : "Off"}
            </span>
          </div>
        </div>
        <Button
          variant="outline"
          disabled={!policy.enabled || updatePolicy.isPending}
          onClick={invalidate}
        >
          Invalidate policy
        </Button>
      </CardContent>
    </Card>
  )
}

function MetadataCard({ policy }: { policy: CrawlPolicyDetailRecord }) {
  const profileConfig = objectValue(policy.config.config)
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Metadata</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-2 text-sm">
        <MetaRow label="ID" value={policy.id} mono />
        <MetaRow label="Metric slug" value={policy.metric_slug} mono />
        <MetaRow label="Domain group" value={policy.domain_group} mono />
        <MetaRow label="URL match" value={policy.url_match_id ?? "-"} mono />
        <MetaRow label="Match" value={policy.match} />
        <MetaRow
          label="Template"
          value={String(profileConfig.template ?? "-")}
        />
        <MetaRow label="Profile" value={String(policy.config.profile ?? "-")} />
        <MetaRow label="Mode" value={String(profileConfig.mode ?? "-")} />
        <MetaRow label="Wait" value={String(profileConfig.wait ?? "-")} />
        <MetaRow
          label="Concurrency"
          value={String(policy.config.concurrency ?? "-")}
        />
        <MetaRow
          label="Cache Blocks"
          value={formatCacheBlocks(profileConfig.cache_block_rules)}
        />
        <MetaRow label="Created" value={formatDate(policy.created_at)} />
        <MetaRow label="Updated" value={formatDate(policy.updated_at)} />
      </CardContent>
    </Card>
  )
}

function objectValue(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {}
  }
  return value as Record<string, unknown>
}

function formatOverrides(value: Record<string, unknown>) {
  const entries = Object.entries(value)
  if (entries.length === 0) {
    return "none"
  }
  return entries
    .map(([key, entryValue]) => `${key}: ${String(entryValue)}`)
    .join(", ")
}

function formatCacheBlocks(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "-"
  }
  const codes = (value as { quality_flag_codes?: unknown })
    .quality_flag_codes
  if (!Array.isArray(codes) || codes.length === 0) {
    return "none"
  }
  return (
    codes
      .filter((code): code is string => typeof code === "string")
      .join(", ") || "none"
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

function formatDate(value: string | null) {
  if (!value) {
    return "-"
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))
}
