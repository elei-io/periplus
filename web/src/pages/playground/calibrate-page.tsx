import type { FormEvent, ReactNode } from "react"
import { useEffect, useMemo, useState } from "react"
import {
  CheckCircle2Icon,
  FlaskConicalIcon,
  RefreshCwIcon,
  XCircleIcon,
  XIcon,
} from "lucide-react"

import { IndexProgress } from "@/components/index-run/index-progress"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useCalibrateRun } from "@/hooks/use-calibrate-run"
import { cn } from "@/lib/utils"
import type { CalibrationCandidate, CalibrationOutput } from "@/types/calibrate"

export function CalibratePage() {
  const calibrateRun = useCalibrateRun()
  const [runKey, setRunKey] = useState(0)
  const initialUrl = useMemo(() => {
    return new URLSearchParams(window.location.search).get("url") ?? ""
  }, [])
  const hasProgress = calibrateRun.events.length > 0
  const showResults =
    calibrateRun.isSuccess && !calibrateRun.isRunning && calibrateRun.result !== null

  return (
    <div
      className={cn(
        "grid min-h-[calc(100svh-7rem)] w-full items-start gap-8 transition-[padding] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]",
        hasProgress ? "content-start py-8" : "content-start pt-[20svh]"
      )}
    >
      <section className="mx-auto grid w-full max-w-4xl gap-5">
        <div className="grid max-w-[42rem] gap-2 text-left">
          <h1 className="text-2xl font-semibold tracking-normal">
            Calibrate crawl policy
          </h1>
          <p className="text-sm leading-6 text-muted-foreground md:text-[0.95rem]">
            Test fixed Crawl4AI transport templates and persist the cheapest
            reliable policy for the page domain.
          </p>
        </div>
        <CalibrateForm
          initialUrl={initialUrl}
          isRunning={calibrateRun.isRunning}
          onCancel={calibrateRun.cancel}
          onSubmit={(input) => {
            setRunKey((currentRunKey) => currentRunKey + 1)
            calibrateRun.run(input)
          }}
        />
        <AnimatedSection
          className="mx-auto w-full max-w-4xl"
          hiddenClassName="-translate-y-1 opacity-0"
          show={hasProgress && !showResults}
          transitionClassName="duration-300"
        >
          <IndexProgress events={calibrateRun.events} />
        </AnimatedSection>
      </section>

      <AnimatedSection
        key={`results-${runKey}`}
        className="mx-auto w-full max-w-6xl"
        hiddenClassName="translate-y-3 opacity-0"
        show={showResults}
        transitionClassName="duration-500"
      >
        {calibrateRun.result ? (
          <CalibrateResults result={calibrateRun.result} />
        ) : null}
      </AnimatedSection>
    </div>
  )
}

type CalibrateFormProps = {
  initialUrl?: string
  isRunning: boolean
  onCancel: () => void
  onSubmit: (input: { url: string; force: boolean }) => void
}

function CalibrateForm({
  initialUrl = "",
  isRunning,
  onCancel,
  onSubmit,
}: CalibrateFormProps) {
  const [url, setUrl] = useState(initialUrl)
  const [force, setForce] = useState(false)
  const [error, setError] = useState("")

  const submit = () => {
    const normalizedUrl = url.trim()
    try {
      const parsedUrl = new URL(normalizedUrl)
      if (!["http:", "https:"].includes(parsedUrl.protocol)) {
        setError("Enter an http or https URL.")
        return
      }
    } catch {
      setError("Enter a valid URL.")
      return
    }

    setError("")
    onSubmit({ url: normalizedUrl, force })
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    submit()
  }

  return (
    <form onSubmit={handleSubmit}>
      <div className="mx-auto grid w-full max-w-4xl gap-3">
        <div className="playground-command-bar flex flex-col gap-2 rounded-3xl border bg-card/85 p-1.5 shadow-[0_18px_60px_rgb(0_0_0/0.18),0_1px_0_rgb(255_255_255/0.06)_inset] backdrop-blur-xl sm:flex-row sm:items-center sm:rounded-full">
          <div className="flex min-w-0 flex-1 items-center gap-2 px-4">
            <FlaskConicalIcon className="size-4 shrink-0 text-muted-foreground" />
            <Input
              className="h-10 rounded-none border-0 bg-transparent px-0 text-base shadow-none focus-visible:border-0 focus-visible:ring-0 dark:bg-transparent"
              type="url"
              placeholder="Calibrate a URL"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              disabled={isRunning}
              autoFocus
              required
            />
          </div>
          <div className="flex items-center gap-3 px-2 sm:shrink-0">
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Switch checked={force} disabled={isRunning} onCheckedChange={setForce} />
              Force
            </label>
            <Button
              className="h-10 flex-1 rounded-full px-5 sm:flex-none"
              type="button"
              disabled={isRunning}
              onClick={submit}
            >
              <FlaskConicalIcon />
              Calibrate
            </Button>
            {isRunning ? (
              <Button
                className="h-10 rounded-full"
                type="button"
                variant="outline"
                onClick={onCancel}
              >
                <XIcon />
                Cancel
              </Button>
            ) : null}
          </div>
        </div>
        {error ? <p className="px-4 text-xs text-destructive">{error}</p> : null}
      </div>
    </form>
  )
}

function CalibrateResults({ result }: { result: CalibrationOutput }) {
  return (
    <Card size="sm" className="overflow-hidden">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="grid gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={result.reused_policy ? "outline" : "secondary"}>
                {result.reused_policy ? <RefreshCwIcon /> : <CheckCircle2Icon />}
                {result.reused_policy ? "Reused" : "Calibrated"}
              </Badge>
              <CardTitle>{result.selected_template}</CardTitle>
            </div>
            <CardDescription>
              {result.match} · policy {result.policy.id}
            </CardDescription>
          </div>
          <Badge variant="outline">{result.candidates.length || 1} templates</Badge>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="rounded-md border bg-muted/20 p-3">
          <pre className="max-h-52 overflow-auto font-mono text-xs leading-5">
            {JSON.stringify(result.selected_config, null, 2)}
          </pre>
        </div>
        {result.candidates.length > 0 ? (
          <Table containerClassName="rounded-md border bg-card/80">
            <TableHeader>
              <TableRow>
                <TableHead>Template</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>HTML</TableHead>
                <TableHead>Text</TableHead>
                <TableHead>Links</TableHead>
                <TableHead>Warnings</TableHead>
                <TableHead>Duration</TableHead>
                <TableHead>Reason</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.candidates.map((candidate) => (
                <CandidateRow key={candidate.template} candidate={candidate} />
              ))}
            </TableBody>
          </Table>
        ) : (
          <div className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">
            Existing enabled policy matched this domain. Enable force to recalibrate.
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function CandidateRow({ candidate }: { candidate: CalibrationCandidate }) {
  return (
    <TableRow>
      <TableCell>
        <div className="flex items-center gap-2">
          <Badge variant={candidate.accepted ? "secondary" : "outline"}>
            {candidate.accepted ? <CheckCircle2Icon /> : null}
            {candidate.template}
          </Badge>
        </div>
        <div className="mt-1 text-xs text-muted-foreground">
          {candidate.mode}/{candidate.wait}
        </div>
      </TableCell>
      <TableCell>
        <Badge variant={candidate.success ? "outline" : "destructive"}>
          {candidate.success ? <CheckCircle2Icon /> : <XCircleIcon />}
          {candidate.status_code ?? "no"} status
        </Badge>
      </TableCell>
      <TableCell>{formatBytes(candidate.quality.html_bytes)}</TableCell>
      <TableCell>{candidate.quality.text_chars.toLocaleString()}</TableCell>
      <TableCell>{candidate.quality.link_count.toLocaleString()}</TableCell>
      <TableCell>
        <Badge variant={candidate.quality.warning_count > 0 ? "destructive" : "outline"}>
          {candidate.quality.warning_count}
        </Badge>
      </TableCell>
      <TableCell>{candidate.duration_seconds.toFixed(2)}s</TableCell>
      <TableCell className="max-w-[22rem] text-sm text-muted-foreground">
        {candidate.reason}
      </TableCell>
    </TableRow>
  )
}

type AnimatedSectionProps = {
  children: ReactNode
  className: string
  hiddenClassName: string
  show: boolean
  transitionClassName: string
}

function AnimatedSection({
  children,
  className,
  hiddenClassName,
  show,
  transitionClassName,
}: AnimatedSectionProps) {
  const [entered, setEntered] = useState(false)

  useEffect(() => {
    if (!show) {
      return undefined
    }

    const frame = window.requestAnimationFrame(() => {
      setEntered(true)
    })

    return () => window.cancelAnimationFrame(frame)
  }, [show])

  if (!show) {
    return null
  }

  return (
    <div
      className={cn(
        "transition-[opacity,transform] ease-[cubic-bezier(0.22,1,0.36,1)]",
        className,
        transitionClassName,
        entered ? "translate-y-0 opacity-100" : hiddenClassName
      )}
    >
      {children}
    </div>
  )
}

function formatBytes(bytes: number) {
  if (bytes < 1024) {
    return `${bytes} B`
  }

  const kib = bytes / 1024
  if (kib < 1024) {
    return `${kib.toFixed(1)} KiB`
  }

  return `${(kib / 1024).toFixed(1)} MiB`
}
