import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { apiUrl, apiErrorFromResponse, extractApiError } from "@/lib/api"
import type { AccessPolicy, Capability } from "@/types/access"

function retentionLabel(seconds: number | null) {
  if (seconds === null) return "forever"
  for (const [unit, factor] of [
    ["d", 86400],
    ["h", 3600],
    ["m", 60],
    ["s", 1],
  ] as const) {
    if (seconds % factor === 0) return `${seconds / factor}${unit}`
  }
  return `${seconds}s`
}
function retentionValue(value: string) {
  const text = value.trim().toLowerCase()
  if (text === "forever") return null
  const match = /^(\d+)([smhd])$/.exec(text)
  if (!match)
    throw new Error(
      "Use a retention duration such as 7d, 24h, 30m, 60s, or forever."
    )
  return (
    Number(match[1]) *
    { s: 1, m: 60, h: 3600, d: 86400 }[match[2] as "s" | "m" | "h" | "d"]
  )
}

export function AccessPage() {
  const cache = useQueryClient()
  const query = useQuery({
    queryKey: ["public-access"],
    queryFn: async ({ signal }) => {
      const response = await fetch(apiUrl("/access"), { signal })
      if (!response.ok) throw await apiErrorFromResponse(response)
      return response.json() as Promise<AccessPolicy>
    },
    refetchInterval: 5000,
  })
  return (
    <div className="w-full space-y-5">
      <h1 className="text-2xl font-semibold">Public access</h1>
      <p>
        Control new public activity. Admin and scheduled work continue under
        normal crawler and resource limits. Started work finishes.
      </p>
      {query.error && <p role="alert">{extractApiError(query.error)}</p>}
      {query.isPending && <p>Loading settings…</p>}
      {query.data && (
        <AccessForm
          key={query.data.version}
          policy={query.data}
          saved={() => {
            void cache.invalidateQueries({ queryKey: ["public-access"] })
          }}
        />
      )}
    </div>
  )
}
function AccessForm({
  policy,
  saved,
}: {
  policy: AccessPolicy
  saved: () => void
}) {
  const mutation = useMutation({
    mutationFn: async (body: unknown) => {
      const response = await fetch(apiUrl("/access"), {
        method: "PUT",
        signal: AbortSignal.timeout(15000),
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      })
      if (!response.ok) throw await apiErrorFromResponse(response)
    },
    onSuccess: () => {
      toast.success("Public access settings saved.")
      saved()
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
  const features: [Capability, string][] = [
    ["crawl", "Public crawl submissions"],
    ["assistant", "Public dataset assistant"],
    ["sql", "Public SQL access"],
  ]
  return (
    <form
      className="space-y-5"
      onSubmit={(event) => {
        event.preventDefault()
        try {
          const form = new FormData(event.currentTarget)
          const integer = (key: string) => {
            const raw = String(form.get(key) ?? "").trim()
            const n = Number(raw)
            if (!raw || !Number.isInteger(n))
              throw new Error(`${key}: enter a whole number`)
            return n
          }
          const values = (key: string) =>
            String(form.get(key))
              .split(",")
              .map((v) => {
                if (!v.trim()) throw new Error("Options cannot be empty")
                if (key === "retention_seconds") return retentionValue(v)
                const n = Number(v)
                if (!Number.isInteger(n))
                  throw new Error(
                    "Page and depth options must be whole numbers"
                  )
                return n
              })
          const body = {
            ...policy,
            expected_version: policy.version,
          } as Record<string, unknown>
          delete body.version
          delete body.crawl_admission
          for (const [key] of features)
            body[key] = {
              ...policy[key],
              enabled: form.has(`${key}.enabled`),
              requests: integer(`${key}.requests`),
              window_seconds: integer(`${key}.window_seconds`),
            }
          body.sql = {
            ...(body.sql as object),
            max_rows: integer("sql.max_rows"),
            max_duration_seconds: integer("sql.max_duration_seconds"),
            max_result_bytes: integer("sql.max_result_mib") * 1024 * 1024,
          }
          body.crawl = {
            ...(body.crawl as object),
            page_budgets: values("page_budgets"),
            default_page_budget: integer("default_page_budget"),
            follow_link_limits: values("follow_link_limits"),
            default_follow_link_limit: integer("default_follow_link_limit"),
            max_depths: values("max_depths"),
            default_max_depth: integer("default_max_depth"),
            retention_seconds: values("retention_seconds"),
            default_retention_seconds: retentionValue(
              String(form.get("default_retention_seconds"))
            ),
          }
          const queueLimit = String(form.get("crawl.queue_limit") ?? "").trim()
          ;(body.crawl as AccessPolicy["crawl"]).queue_limit = queueLimit ? integer("crawl.queue_limit") : null
          mutation.mutate(body)
        } catch (error) {
          toast.error(extractApiError(error))
        }
      }}
    >
      {features.map(([key, label]) => (
        <Card key={key}>
          <CardHeader>
            <CardTitle>{label}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <label className="flex gap-2">
              <Checkbox
                name={`${key}.enabled`}
                defaultChecked={policy[key].enabled}
              />
              Enabled
            </label>
            <div className="grid gap-4 sm:grid-cols-2">
              <label>
                Requests per window
                <Input
                  name={`${key}.requests`}
                  type="number"
                  min={1}
                  max={1000000}
                  required
                  defaultValue={policy[key].requests}
                />
              </label>
              <label>
                Window (seconds)
                <Input
                  name={`${key}.window_seconds`}
                  type="number"
                  min={1}
                  max={86400}
                  required
                  defaultValue={policy[key].window_seconds}
                />
              </label>
            </div>
            <p className="text-sm text-muted-foreground">
              Global across all public visitors and replicas.{" "}
              {key === "sql"
                ? "Controls direct SQL, including dataset reruns. Catalogue browsing and assistant queries remain available."
                : key === "assistant"
                  ? "Counts new assistant turns. Internal assistant queries use their own execution bounds."
                  : "Counts new submissions. Existing executions and retries of accepted identities continue."}
            </p>
            {key === "sql" && (
              <div className="space-y-4">
                <div className="grid gap-4 sm:grid-cols-2">
                  <label>
                    Maximum result rows
                    <Input name="sql.max_rows" type="number" min={1} max={10000} step={1} required defaultValue={policy.sql.max_rows} />
                  </label>
                  <label>
                    Maximum duration (seconds)
                    <Input name="sql.max_duration_seconds" type="number" min={1} max={120} step={1} required defaultValue={policy.sql.max_duration_seconds} />
                  </label>
                  <label>
                    Maximum result size (MiB)
                    <Input name="sql.max_result_mib" type="number" min={1} max={64} step={1} required defaultValue={policy.sql.max_result_bytes / (1024 * 1024)} />
                  </label>
                </div>
                <p className="text-sm text-muted-foreground">
                  Execution limits apply to all new read-only query-service operations, including SDK,
                  assistant and scheduled seed queries. Running queries keep their starting limits.
                  Results explicitly report truncation when they reach the row or size limit. Duration includes preparation.
                  Administrative SQL has separate permissions and limits.
                </p>
              </div>
            )}
            {key === "crawl" && (
              <div className="grid gap-4 sm:grid-cols-2">
                <label>
                  Pause new public coverage requests at queue size
                  <Input name="crawl.queue_limit" type="number" min={1} max={1000000000} step={1}
                    defaultValue={policy.crawl.queue_limit ?? ""} placeholder="Unlimited" />
                </label>
                <p className="text-sm text-muted-foreground">
                  Queued and retrying acquisitions: {policy.crawl_admission.pending_acquisitions.toLocaleString()}.
                  New submissions {policy.crawl_admission.accepting ? "are open" : "are paused"}.
                  Leave blank for unlimited. Existing requests and admin submissions continue;
                  public submissions reopen automatically below the threshold.
                </p>
                {(
                  [
                    ["page_budgets", "default_page_budget", "Page budget"],
                    ["max_depths", "default_max_depth", "Maximum depth"],
                    ["follow_link_limits", "default_follow_link_limit", "Maximum links per page"],
                    [
                      "retention_seconds",
                      "default_retention_seconds",
                      "Retention",
                    ],
                  ] as const
                ).map(([field, defaultField, label]) => (
                  <div key={field} className="contents">
                    <label>
                      {label} options
                      <Input
                        name={field}
                        required
                        defaultValue={policy.crawl[field]
                          .map((v) =>
                            field === "retention_seconds"
                              ? retentionLabel(v)
                              : v
                          )
                          .join(", ")}
                      />
                      <span className="text-xs">
                        {field === "retention_seconds"
                          ? "Comma-separated durations: 7d, 30d, forever"
                          : "Comma-separated values"}
                      </span>
                    </label>
                    <label>
                      Default {label.toLowerCase()}
                      <Input
                        name={defaultField}
                        required
                        type={field === "retention_seconds" ? "text" : "number"}
                        defaultValue={
                          field === "retention_seconds"
                            ? retentionLabel(policy.crawl[defaultField])
                            : (policy.crawl[defaultField] ?? "")
                        }
                      />
                    </label>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      ))}
      <Button type="submit" disabled={mutation.isPending}>
        Save access settings
      </Button>
    </form>
  )
}
