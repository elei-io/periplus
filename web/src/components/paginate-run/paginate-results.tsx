import { RouteIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { PaginateOutput } from "@/types/paginate"

type PaginateResultsProps = {
  result: PaginateOutput
}

export function PaginateResults({ result }: PaginateResultsProps) {
  return (
    <Card size="sm" className="flex min-h-[420px] flex-col overflow-hidden">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="grid gap-1">
            <div className="flex items-center gap-2">
              <Badge variant="secondary">
                <RouteIcon />
                Pagination
              </Badge>
              <CardTitle>{result.pages.length} pages</CardTitle>
            </div>
            <CardDescription>
              Stopped: {result.stopped_reason}
            </CardDescription>
          </div>
          {result.plan ? (
            <div className="grid gap-1 text-right text-xs">
              <Badge variant={result.plan.reused ? "secondary" : "outline"}>
                {result.plan.reused ? "reused" : "generated"}
              </Badge>
              <span className="font-mono text-muted-foreground">{result.plan.kind}</span>
            </div>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="grid min-h-0 flex-1 gap-4">
        {result.plan ? (
          <div className="grid gap-2 rounded-md border bg-muted/20 p-3 text-xs">
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">kind: {result.plan.kind}</Badge>
              {typeof result.plan.confidence === "number" ? (
                <Badge variant="outline">confidence: {Math.round(result.plan.confidence * 100)}%</Badge>
              ) : null}
            </div>
            <div className="grid gap-1">
              <span className="text-muted-foreground">Item selector</span>
              <code className="break-all rounded-sm bg-background px-2 py-1">{result.plan.item_selector}</code>
            </div>
            {result.plan.next_button_selector ? (
              <div className="grid gap-1">
                <span className="text-muted-foreground">Next selector</span>
                <code className="break-all rounded-sm bg-background px-2 py-1">
                  {result.plan.next_button_selector}
                </code>
              </div>
            ) : null}
            <div className="grid gap-1">
              <span className="text-muted-foreground">Query template</span>
              <code className="break-all rounded-sm bg-background px-2 py-1">
                {result.plan.query_param_key}={result.plan.query_param_value_template}
                {"  "}start {result.plan.start_value}, step {result.plan.value_step}
              </code>
            </div>
          </div>
        ) : null}

        {result.warnings.length > 0 ? (
          <div className="grid gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs">
            <span className="font-medium text-destructive">Validation notes</span>
            <ul className="grid gap-1 text-muted-foreground">
              {result.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        ) : null}

        <Table containerClassName="rounded-md border">
          <TableHeader>
            <TableRow>
              <TableHead>Page</TableHead>
              <TableHead>URL</TableHead>
              <TableHead>Items</TableHead>
              <TableHead>New</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {result.pages.map((page) => (
              <TableRow key={`${page.index}-${page.url}`}>
                <TableCell>{page.index + 1}</TableCell>
                <TableCell className="max-w-[34rem]">
                  <span className="block truncate font-mono text-xs">{page.url}</span>
                </TableCell>
                <TableCell>{page.item_count}</TableCell>
                <TableCell>{page.new_item_count}</TableCell>
                <TableCell>
                  {page.success ? (
                    <Badge variant="secondary">ok</Badge>
                  ) : (
                    <Badge variant="destructive">{page.error ?? "failed"}</Badge>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}
