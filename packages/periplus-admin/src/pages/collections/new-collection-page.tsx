import { useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Checkbox } from "@/components/ui/checkbox"
import { useSubmitCollection } from "@/hooks/use-collections"
import { extractApiError } from "@/lib/api"
import type { CreateCollection } from "@/types/collections"
import { collectionSubmission } from "./submission"

export function NewCollectionPage() {
  const mutation = useSubmitCollection()
  const [frozen, setFrozen] = useState<CreateCollection | null>(null)
  const send = (payload: CreateCollection) =>
    mutation.mutate(payload, {
      onSuccess: (value) => {
        window.location.assign(`/collections/${value.id}`)
      },
    })
  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <a className="underline" href="/collections">
        All collections
      </a>
      <h1 className="text-2xl font-semibold">New collection</h1>
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
            setFrozen(payload)
            send(payload)
          } catch (error) {
            toast.error(extractApiError(error))
          }
        }}
      >
        <fieldset
          disabled={frozen !== null}
          className="flex min-w-0 flex-col gap-5"
        >
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
                placeholder="https://example.com/"
              />
              <label htmlFor="seed-description">Source description</label>
              <Textarea
                id="seed-description"
                name="seed_description"
                maxLength={4000}
                placeholder="Describe the sources you want to discover"
              />
              <label htmlFor="seed-sql">Seed SQL</label>
              <Textarea
                id="seed-sql"
                name="seed_sql"
                maxLength={20000}
                placeholder="SELECT requested_url AS url FROM web.observation LIMIT 25"
              />
              <label htmlFor="seed-parameters">
                Seed SQL parameters (JSON array)
              </label>
              <Textarea
                id="seed-parameters"
                name="seed_parameters"
                defaultValue="[]"
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
                defaultValue="SELECT target_url AS url FROM nav.links"
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
                defaultValue={0}
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
                defaultValue={25}
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
                defaultValue={300}
              />
              <label htmlFor="sections">
                Allowed URL sections (one per line)
              </label>
              <Textarea
                id="sections"
                name="allowed_sections"
                placeholder="https://example.com/articles"
              />
              <label htmlFor="deadline">Deadline (your local time)</label>
              <Input id="deadline" name="deadline_at" type="datetime-local" />
              <p className="text-sm text-muted-foreground">
                Depth zero captures starting pages only. Background exploration
                may independently continue from public results under its own
                allowance.
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Visibility and access</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <label className="flex items-center gap-2">
                <Checkbox name="private" />
                Private collection
              </label>
              <p className="text-sm text-muted-foreground">
                Private evidence does not appear in the public catalogue or seed
                public background exploration. Private collections do not share
                acquisitions across requests.
              </p>
              <label htmlFor="access-context">Access context</label>
              <Input
                id="access-context"
                name="access_context"
                maxLength={200}
                required
                defaultValue="public"
              />
            </CardContent>
          </Card>
        </fieldset>
        {!frozen && (
          <Button type="submit" className="self-start">
            Submit collection
          </Button>
        )}
        {frozen && (
          <div className="flex flex-col gap-3" role="status">
            <p className="break-all">
              Request identity:{" "}
              <a className="underline" href={`/collections/${frozen.id}`}>
                {frozen.id}
              </a>
            </p>
            {mutation.isPending ? (
              <p>Submitting collection…</p>
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
                    To change the intent, open a new collection form. This
                    request may already have been accepted.
                  </p>
                  <a className="underline" href="/collections/new">
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
