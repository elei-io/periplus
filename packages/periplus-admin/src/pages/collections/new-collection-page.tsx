import { useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useSubmitCollection } from "@/hooks/use-collections"
import { extractApiError } from "@/lib/api"
import type { CreateCollection } from "@/types/collections"
import { useScheduleAction } from "@/hooks/use-schedules"
import type { RequestDefinition } from "@/types/schedules"
import { collectionSubmission } from "./submission"

export function NewCollectionPage({
  reusable = false,
  definition,
}: {
  reusable?: boolean
  definition?: RequestDefinition
}) {
  const saveDefinition = useScheduleAction()
  const spec = definition?.specification
  const mutation = useSubmitCollection()
  const [frozen, setFrozen] = useState<CreateCollection | null>(null)
  const send = (payload: CreateCollection) =>
    mutation.mutate(payload, {
      onSuccess: (value) => {
        window.location.assign(`/observatory/executions/${value.id}`)
      },
    })
  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <a className="underline" href={reusable ? "/observatory/requests" : "/observatory/executions"}>
        {reusable ? "All requests" : "All executions"}
      </a>
      <h1 className="text-2xl font-semibold">
        {reusable
          ? definition
            ? "Edit request"
            : "New request"
          : "Run once"}
      </h1>
      <p>
        Give the crawler starting URLs, a source description, or a bounded SQL
        selection. Multiple sources can contribute to the same request.
      </p>
      <form
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault()
          if (frozen) return
          try {
            const payload = {
              id: crypto.randomUUID(),
              specification: collectionSubmission(
                new FormData(event.currentTarget)
              ),
              priority: 0,
            }
            if (reusable) {
              const form = new FormData(event.currentTarget)
              saveDefinition.mutate(
                {
                  path: definition
                    ? `/request-definitions/${definition.id}`
                    : "/request-definitions",
                  method: definition ? "PUT" : "POST",
                  body: {
                    name: String(form.get("definition_name")),
                    specification: payload.specification,
                    priority: Number(form.get("priority") ?? 0),
                    ...(definition
                      ? { expected_version: definition.version }
                      : {}),
                  },
                },
                {
                  onSuccess: () =>
                    window.location.assign(definition ? `/observatory/requests/${definition.id}` : "/observatory/requests"),
                }
              )
              return
            }
            setFrozen(payload)
            send(payload)
          } catch (error) {
            toast.error(extractApiError(error))
          }
        }}
      >
        <fieldset
          disabled={frozen !== null || saveDefinition.isPending}
          className="flex min-w-0 flex-col gap-5"
        >
          {reusable && (
            <Card>
              <CardContent className="space-y-3 pt-5">
                <label htmlFor="definition-name">Request name</label>
                <Input
                  id="definition-name"
                  name="definition_name"
                  required
                  maxLength={200}
                  defaultValue={definition?.name}
                />
                <label htmlFor="definition-priority">
                  Priority (-10 to 10)
                </label>
                <Input
                  id="definition-priority"
                  name="priority"
                  type="number"
                  min={-10}
                  max={10}
                  step={1}
                  required
                  defaultValue={definition?.priority ?? 0}
                />
                <p className="text-xs text-muted-foreground">
                  Edits apply only to future executions. Each run reevaluates
                  selection and freezes its own intent and seeds.
                </p>
              </CardContent>
            </Card>
          )}
          <Card>
            <CardHeader>
              <CardTitle>Starting sources</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <label htmlFor="seed-urls">
                Starting URLs (one per line, up to 1,000)
              </label>
              <Textarea
                id="seed-urls"
                name="seed_urls"
                defaultValue={spec?.seed_urls.join("\n")}
                placeholder="https://example.com/"
              />
              <label htmlFor="seed-description">Source description</label>
              <Textarea
                id="seed-description"
                name="seed_description"
                defaultValue={spec?.seed_description ?? ""}
                maxLength={4000}
                placeholder="Describe the sources you want to discover"
              />
              <label htmlFor="seed-sql">Seed SQL</label>
              <Textarea
                id="seed-sql"
                name="seed_sql"
                defaultValue={spec?.seed_sql ?? ""}
                maxLength={20000}
                placeholder="SELECT requested_url AS url FROM web.observation LIMIT 25"
              />
              <label htmlFor="seed-parameters">
                Seed SQL parameters (JSON array)
              </label>
              <Textarea
                id="seed-parameters"
                name="seed_parameters"
                defaultValue={JSON.stringify(spec?.seed_parameters ?? [])}
              />
              <p className="text-sm text-muted-foreground">
                Seed SQL reads the catalogue once to select starting URLs. The
                server validates and freezes selection before admission.
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Traversal and budget</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <label htmlFor="follow-sql">Follow-link SQL</label>
              <Textarea
                id="follow-sql"
                name="follow_sql"
                required
                maxLength={20000}
                defaultValue={
                  spec?.follow_sql ?? "SELECT target_url AS url FROM nav.links"
                }
              />
              <p className="text-sm text-muted-foreground">
                Runs over each page’s navigation package, within this request’s
                depth, scope, and budget. It cannot join historical catalogue
                tables.
              </p>
              <label htmlFor="max-depth">Maximum depth</label>
              <Input
                id="max-depth"
                name="max_depth"
                type="number"
                min={0}
                max={100}
                step={1}
                required
                defaultValue={spec?.max_depth ?? 0}
              />
              <label htmlFor="page-limit">Page budget</label>
              <Input
                id="page-limit"
                name="page_limit"
                type="number"
                min={1}
                max={100000}
                step={1}
                required
                defaultValue={spec?.page_limit ?? 25}
              />
              <label htmlFor="retention">
                Retention after completion (seconds; blank means forever)
              </label>
              <Input
                id="retention"
                name="retention_seconds"
                defaultValue={spec?.retention_seconds ?? ""}
                type="number"
                min={1}
                max={315360000}
                step={1}
                placeholder="Forever"
              />
              <label htmlFor="reuse-age">
                Maximum recent-result age (seconds; zero requires fresh
                acquisition)
              </label>
              <Input
                id="reuse-age"
                name="result_max_age_seconds"
                type="number"
                min={0}
                max={3600}
                step={1}
                required
                defaultValue={spec?.result_max_age_seconds ?? 300}
              />
              <label htmlFor="sections">
                Allowed URL sections (one per line)
              </label>
              <Textarea
                id="sections"
                name="allowed_sections"
                defaultValue={spec?.allowed_sections.join("\n")}
                placeholder="https://example.com/articles"
              />
              <label htmlFor="max-duration">Maximum duration (seconds, optional)</label>
              <Input id="max-duration" name="max_duration_seconds" type="number"
                min={1} max={31536000} step={1}
                defaultValue={spec?.max_duration_seconds ?? ""} placeholder="No time limit" />
              <p className="text-sm text-muted-foreground">
                Starts when the request is created, including waiting and paused time.
                Already-started captures finish after the limit.
              </p>
              <p className="text-sm text-muted-foreground">
                Depth zero captures starting pages only. Each execution stops at
                its own traversal and budget limits.
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Request class</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <label htmlFor="request-class">Purpose</label>
              <Select name="request_class" defaultValue={spec?.request_class ?? "admin"}><SelectTrigger id="request-class"><SelectValue /></SelectTrigger><SelectContent>
                <SelectItem value="admin">Admin · your data project</SelectItem>
                <SelectItem value="system">System · ongoing Periplus work</SelectItem>
              </SelectContent></Select>
              <p className="text-sm text-muted-foreground">All requests contribute to the shared catalogue and can share acquisitions. Class identifies purpose; it does not restrict data access.</p>
            </CardContent>
          </Card>
        </fieldset>
        {!frozen && (
          <Button
            type="submit"
            className="self-start"
            disabled={saveDefinition.isPending}
          >
            {reusable ? "Save request" : "Run once"}
          </Button>
        )}
        {frozen && (
          <div className="flex flex-col gap-3" role="status">
            <p className="break-all">
              Request identity:{" "}
              <a
                className="underline"
                href={`/observatory/executions/${frozen.id}`}
              >
                {frozen.id}
              </a>
            </p>
            {mutation.isPending ? (
              <p>Submitting request…</p>
            ) : (
              mutation.isError && (
                <>
                  <p role="alert">
                    Submission was not confirmed.{" "}
                    {extractApiError(mutation.error)} Check the request or retry
                    with the same frozen intent and identity.
                  </p>
                  <Button
                    type="button"
                    className="self-start"
                    onClick={() => send(frozen)}
                  >
                    Retry same request
                  </Button>
                  <p className="text-sm text-muted-foreground">
                    To change the intent, open a new request form. This request
                    may already have been accepted.
                  </p>
                  <a className="underline" href="/observatory/executions/new">
                    Start a different request
                  </a>
                </>
              )
            )}
          </div>
        )}
      </form>
    </div>
  )
}
