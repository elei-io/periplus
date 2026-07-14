import {
  AlertCircleIcon,
  CheckCircle2Icon,
  ChevronRightIcon,
  Clock3Icon,
  FlaskConicalIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
} from "lucide-react"
import { useState } from "react"

import {
  RESOURCE_PAGE_SIZE,
  ResourcePagination,
} from "@/components/resources/resource-pagination"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  useApplyPolicyTrial,
  usePolicyTrials,
} from "@/hooks/use-resource-data"
import { extractApiError } from "@/lib/api"
import type {
  PolicyTrialComparison,
  PolicyTrialSummary,
} from "@/types/resources"

export function PolicyTrialsPage() {
  const [offset, setOffset] = useState(0)
  const [applyComparison, setApplyComparison] = useState<PolicyTrialComparison | null>(null)
  const trialsQuery = usePolicyTrials({ limit: RESOURCE_PAGE_SIZE, offset })
  const applyTrial = useApplyPolicyTrial()
  const report = trialsQuery.data

  return (
    <div className="flex w-full self-start flex-col gap-4">
      <section className="flex flex-col gap-3 border-b pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <FlaskConicalIcon className="size-4 text-muted-foreground" />
            <h1 className="truncate text-lg font-medium">Policy trials</h1>
            {report ? (
              <Badge variant={report.summary.sampling_active ? "secondary" : "outline"}>
                {report.summary.sampling_active ? "Sampling" : "Off"}
              </Badge>
            ) : null}
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={trialsQuery.isFetching}
            onClick={() => void trialsQuery.refetch()}
          >
            <RefreshCwIcon className={trialsQuery.isFetching ? "animate-spin" : ""} />
            Refresh
          </Button>
        </div>
      </section>

      {trialsQuery.isLoading ? <TrialsSkeleton /> : null}
      {trialsQuery.isError ? (
        <Card>
          <CardContent className="flex items-center gap-2 py-6 text-sm text-destructive">
            <AlertCircleIcon className="size-4" />
            {extractApiError(trialsQuery.error)}
          </CardContent>
        </Card>
      ) : null}
      {report ? (
        <>
          <SamplingSummary summary={report.summary} />
          <ComparisonEvidence
            comparisons={report.items}
            samplingActive={report.summary.sampling_active}
            onApply={setApplyComparison}
          />
          <ResourcePagination
            total={report.total}
            limit={report.limit}
            offset={report.offset}
            isFetching={trialsQuery.isFetching}
            onOffsetChange={setOffset}
          />
        </>
      ) : null}

      <ApplyPolicyDialog
        comparison={applyComparison}
        pending={applyTrial.isPending}
        onOpenChange={(open) => {
          if (!open && !applyTrial.isPending) setApplyComparison(null)
        }}
        onApply={(comparison) => {
          applyTrial.mutate(
            {
              scheme: comparison.scheme,
              host: comparison.host,
              port: comparison.port,
              template: comparison.candidate_template,
            },
            { onSuccess: () => setApplyComparison(null) }
          )
        }}
      />
    </div>
  )
}

function SamplingSummary({ summary }: { summary: PolicyTrialSummary }) {
  const pairRate = summary.selected_trials
    ? summary.completed_pairs / summary.selected_trials
    : 0

  return (
    <Card>
      <CardHeader className="border-b">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>
              {summary.sampling_active ? "Sampling is active" : "Sampling is off"}
            </CardTitle>
            <CardDescription className="mt-1 max-w-2xl">
              {summary.sampling_active
                ? `${formatPercent(summary.configured_sample_rate)} of ordinary crawls are selected for one shadow acquisition with the next policy template.`
                : "No new trials are being selected. Historical evidence remains available below."}
            </CardDescription>
          </div>
          <Badge variant={summary.sampling_active ? "secondary" : "outline"}>
            {summary.sampling_active ? <CheckCircle2Icon /> : <Clock3Icon />}
            {summary.sampling_active ? "Collecting evidence" : "Historical only"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid gap-x-6 gap-y-5 sm:grid-cols-2 xl:grid-cols-4">
          <SummaryMetric
            label="Current sample rate"
            value={formatPercent(summary.configured_sample_rate)}
            detail={`At most ${summary.max_in_flight.toLocaleString()} samples in flight`}
          />
          <SummaryMetric
            label="Observed rate · all time"
            value={formatPercent(summary.observed_sample_rate)}
            detail={`${summary.selected_trials.toLocaleString()} of ${summary.use_crawls.toLocaleString()} use crawls selected`}
          />
          <SummaryMetric
            label="Completed pairs"
            value={summary.completed_pairs.toLocaleString()}
            detail={`${formatPercent(pairRate)} of selected trials · ${summary.sample_crawls.toLocaleString()} samples recorded`}
          />
          <SummaryMetric
            label="Awaiting a sample"
            value={summary.awaiting_samples.toLocaleString()}
            detail={
              summary.pairs_with_failure
                ? `${summary.pairs_with_failure.toLocaleString()} completed pairs include an acquisition failure`
                : "No completed pairs include an acquisition failure"
            }
          />
        </div>
        {summary.last_trial_at ? (
          <p className="mt-5 border-t pt-3 text-xs text-muted-foreground">
            Last trial selected {relativeTime(summary.last_trial_at)}.
          </p>
        ) : null}
      </CardContent>
    </Card>
  )
}

function SummaryMetric({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail: string
}) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
    </div>
  )
}

function ComparisonEvidence({
  comparisons,
  samplingActive,
  onApply,
}: {
  comparisons: PolicyTrialComparison[]
  samplingActive: boolean
  onApply: (comparison: PolicyTrialComparison) => void
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Policy comparisons</CardTitle>
        <CardDescription>
          Expand a comparison to inspect the evidence behind Atlas&apos;s verdict.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table containerClassName="rounded-md border">
          <TableHeader className="bg-muted/30">
            <TableRow className="hover:bg-transparent">
              <TableHead className="min-w-32 pl-3">Domain</TableHead>
              <TableHead className="min-w-40">Trial policy</TableHead>
              <TableHead className="w-16">Pairs</TableHead>
              <TableHead className="min-w-44">Verdict</TableHead>
              <TableHead className="w-16 pr-3 text-right">Action</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {comparisons.map((comparison) => (
              <ComparisonRow
                key={`${comparison.scheme}:${comparison.host}:${comparison.port}:${comparison.use_template}:${comparison.candidate_template}`}
                comparison={comparison}
                onApply={() => onApply(comparison)}
              />
            ))}
            {comparisons.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="h-32 text-center">
                  <p className="font-medium">No policy trials yet</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {samplingActive
                      ? "Atlas is sampling; evidence will appear after both acquisitions are ingested."
                      : "Set ATLAS_POLICY_TRIAL_SAMPLE_SHARE above zero to begin collecting paired evidence."}
                  </p>
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function ComparisonRow({
  comparison,
  onApply,
}: {
  comparison: PolicyTrialComparison
  onApply: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const detailId = `trial-${comparison.scheme}-${comparison.host}-${comparison.port}-${comparison.use_template}-${comparison.candidate_template}`

  return (
    <>
      <TableRow aria-expanded={expanded}>
        <TableCell className="py-3 pl-3 whitespace-normal">
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={`${expanded ? "Hide" : "Show"} evidence for ${originLabel(comparison)}`}
              aria-controls={detailId}
              aria-expanded={expanded}
              onClick={() => setExpanded((value) => !value)}
            >
              <ChevronRightIcon
                className={`transition-transform ${expanded ? "rotate-90" : ""}`}
              />
            </Button>
            <div>
              <p className="font-medium">{originLabel(comparison)}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Last trial {relativeTime(comparison.last_trial_at)}
              </p>
            </div>
          </div>
        </TableCell>
        <TableCell className="py-3 whitespace-normal">
          <Badge variant="outline">
            {formatTemplate(comparison.use_template)} → {formatTemplate(comparison.candidate_template)}
          </Badge>
        </TableCell>
        <TableCell className="py-3 whitespace-normal">
          <p className="font-medium tabular-nums">
            {comparison.completed_pairs.toLocaleString()} / {comparison.selected_trials.toLocaleString()}
          </p>
        </TableCell>
        <TableCell className="py-3 whitespace-normal">
          <VerdictBadge verdict={comparison.verdict} />
        </TableCell>
        <TableCell className="py-3 pr-3 text-right whitespace-normal">
          {comparison.applied ? (
            <Badge variant="secondary">
              <CheckCircle2Icon />
              Applied
            </Badge>
          ) : (
            <Button
              size="sm"
              variant="outline"
              disabled={comparison.completed_pairs === 0}
              onClick={onApply}
            >
              Apply
            </Button>
          )}
        </TableCell>
      </TableRow>
      {expanded ? (
        <TableRow id={detailId} className="hover:bg-transparent">
          <TableCell colSpan={5} className="bg-muted/15 px-4 py-4 whitespace-normal">
            <ComparisonFeatures comparison={comparison} />
          </TableCell>
        </TableRow>
      ) : null}
    </>
  )
}

function VerdictBadge({ verdict }: { verdict: PolicyTrialComparison["verdict"] }) {
  const labels: Record<PolicyTrialComparison["verdict"], string> = {
    awaiting_sample: "Awaiting sample",
    insufficient_evidence: "Insufficient evidence",
    promising: "Promising",
    no_clear_gain: "No clear gain",
    regressed: "Regressed",
    inconclusive: "Inconclusive",
  }
  return (
    <Badge variant={verdict === "regressed" ? "destructive" : verdict === "promising" ? "secondary" : "outline"}>
      {labels[verdict]}
    </Badge>
  )
}

function ComparisonFeatures({ comparison }: { comparison: PolicyTrialComparison }) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-medium">{comparison.verdict_reason}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Features compare {formatTemplate(comparison.use_template)} with {formatTemplate(comparison.candidate_template)} for the same URLs.
          </p>
        </div>
        <div className="text-right">
          <p className="text-[0.625rem] text-muted-foreground uppercase">Current policy</p>
          <p className="mt-1 font-medium">{formatTemplate(comparison.current_template)}</p>
        </div>
      </div>
      <div className="grid gap-px overflow-hidden rounded-md border bg-border sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        <Feature
          label="Visible-text variation"
          value={formatProgression(comparison.use_visible_text_cv, comparison.sample_visible_text_cv, formatPercent)}
          detail={formatTextDistribution(comparison)}
        />
        <Feature
          label="Distinct documents"
          value={formatProgression(comparison.use_distinct_document_ratio, comparison.sample_distinct_document_ratio, formatPercent)}
          detail="Unique documents per distinct sampled URL"
        />
        <Feature
          label="Visible text"
          value={formatNullableSignedPercent(comparison.median_visible_text_delta_percent)}
          detail="Median paired change"
        />
        <Feature
          label="Elements"
          value={formatNullableSignedPercent(comparison.median_element_delta_percent)}
          detail="Median paired change"
        />
        <Feature
          label="HTML bytes"
          value={formatNullableSignedPercent(comparison.median_html_delta_percent)}
          detail="Median paired change"
        />
        <Feature
          label="Identical documents"
          value={`${comparison.identical_documents.toLocaleString()} / ${comparison.completed_pairs.toLocaleString()}`}
          detail="Exact matches across completed pairs"
        />
        <Feature
          label="Quality flags"
          value={formatQualityFlagProgression(comparison)}
          detail="Median count"
        />
        <Feature
          label="Acquisition errors"
          value={`${comparison.use_acquisition_failure_count.toLocaleString()} → ${comparison.sample_acquisition_failure_count.toLocaleString()}`}
          detail={`${comparison.recovered_crawls.toLocaleString()} recovered · ${comparison.sample_failures.toLocaleString()} lost`}
        />
        <Feature
          label="Added time"
          value={formatDurationDelta(comparison.median_duration_delta_ms)}
          detail="Median paired change"
        />
      </div>
    </div>
  )
}

function Feature({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="min-w-0 bg-card p-3">
      <p className="text-[0.625rem] text-muted-foreground uppercase">{label}</p>
      <p className="mt-1 font-medium tabular-nums">{value}</p>
      <p className="mt-1 text-[0.625rem] text-muted-foreground">{detail}</p>
    </div>
  )
}

function ApplyPolicyDialog({
  comparison,
  pending,
  onOpenChange,
  onApply,
}: {
  comparison: PolicyTrialComparison | null
  pending: boolean
  onOpenChange: (open: boolean) => void
  onApply: (comparison: PolicyTrialComparison) => void
}) {
  return (
    <Dialog open={comparison !== null} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            Apply {comparison ? formatTemplate(comparison.candidate_template) : "sampled policy"} to {comparison ? originLabel(comparison) : "domain"}?
          </DialogTitle>
          <DialogDescription>
            Atlas will create or update the broad domain policy for future crawls. More-specific path policies will continue to override it.
          </DialogDescription>
        </DialogHeader>
        {comparison ? (
          <div className="grid grid-cols-2 gap-3 rounded-md border bg-muted/20 p-3 sm:grid-cols-4">
            <DialogMetric
              label="Evidence"
              value={formatEvidenceDelta(comparison)}
            />
            <DialogMetric
              label="Quality flags"
              value={formatQualityFlagProgression(comparison)}
            />
            <DialogMetric
              label="Acquisition errors"
              value={`${comparison.use_acquisition_failure_count.toLocaleString()} → ${comparison.sample_acquisition_failure_count.toLocaleString()}`}
            />
            <DialogMetric
              label="Added time"
              value={formatDurationDelta(comparison.median_duration_delta_ms)}
            />
          </div>
        ) : null}
        <DialogFooter showCloseButton>
          <Button
            disabled={!comparison || pending}
            onClick={() => {
              if (comparison) onApply(comparison)
            }}
          >
            {pending ? <LoaderCircleIcon className="animate-spin" /> : null}
            {pending ? "Applying…" : "Apply policy"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function DialogMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[0.625rem] text-muted-foreground uppercase">{label}</p>
      <p className="mt-1 font-medium tabular-nums">{value}</p>
    </div>
  )
}

function TrialsSkeleton() {
  return (
    <div className="space-y-4" aria-label="Loading policy trials">
      <Skeleton className="h-48 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  )
}

function originLabel(comparison: PolicyTrialComparison) {
  const defaultPort = comparison.scheme === "https" ? 443 : 80
  return comparison.port === defaultPort
    ? comparison.host
    : `${comparison.host}:${comparison.port}`
}

function formatTemplate(value: string) {
  return value.replaceAll("_", " ")
}

function formatEvidenceDelta(comparison: PolicyTrialComparison) {
  const delta =
    comparison.median_element_delta_percent ?? comparison.median_html_delta_percent
  if (delta === null) return "Not comparable"
  const label =
    comparison.median_element_delta_percent !== null ? "elements" : "HTML bytes"
  return `${formatSignedPercent(delta)} ${label}`
}

function formatQualityFlagProgression(comparison: PolicyTrialComparison) {
  const useValue = comparison.median_use_quality_flag_count
  const sampleValue = comparison.median_sample_quality_flag_count
  if (useValue === null || sampleValue === null) return "—"
  return `${formatCount(useValue)} → ${formatCount(sampleValue)}`
}

function formatProgression(
  useValue: number | null,
  sampleValue: number | null,
  formatter: (value: number) => string
) {
  if (useValue === null || sampleValue === null) return "Not enough data"
  return `${formatter(useValue)} → ${formatter(sampleValue)}`
}

function formatTextDistribution(comparison: PolicyTrialComparison) {
  return `${formatDistributionArm(
    comparison.mean_use_visible_text_chars,
    comparison.use_visible_text_stddev
  )} → ${formatDistributionArm(
    comparison.mean_sample_visible_text_chars,
    comparison.sample_visible_text_stddev
  )}`
}

function formatDistributionArm(mean: number | null, stddev: number | null) {
  if (mean === null) return "—"
  const average = `${Math.round(mean).toLocaleString()} avg chars`
  return stddev === null
    ? average
    : `${average} · σ ${Math.round(stddev).toLocaleString()}`
}

function formatNullableSignedPercent(value: number | null) {
  return value === null ? "Not comparable" : formatSignedPercent(value)
}

function formatCount(value: number) {
  return value.toLocaleString(undefined, { maximumFractionDigits: 1 })
}

function formatPercent(value: number) {
  return new Intl.NumberFormat(undefined, {
    style: "percent",
    minimumFractionDigits: value > 0 && value < 0.01 ? 2 : value > 0 ? 1 : 0,
    maximumFractionDigits: 2,
  }).format(value)
}

function formatSignedPercent(value: number) {
  return `${value > 0 ? "+" : ""}${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`
}

function formatDurationDelta(value: number | null) {
  if (value === null) return "—"
  const sign = value > 0 ? "+" : value < 0 ? "−" : ""
  return `${sign}${formatDuration(Math.abs(value))}`
}

function formatDuration(value: number) {
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${Math.round(value)}ms`
}

function relativeTime(value: string) {
  const milliseconds = Math.max(0, Date.now() - new Date(value).getTime())
  const seconds = Math.floor(milliseconds / 1000)
  if (seconds < 10) return "just now"
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}
