# Request definitions and schedules

Reusable request definitions own a name, version, priority and ordinary collection
specification. Schedules reference a definition; each execution freezes the current
specification plus `origin.definition_id`, `definition_version` and optional
`schedule_id`. Origin travels in the existing immutable collection specification,
so it survives control-state retirement. Manual one-off requests have no origin;
Run now from a definition has a definition origin and no schedule ID.

The admin Requests page links to Definitions & schedules. Operators can create and
edit definitions, run them now, add/edit schedules, and pause/resume schedules.
Edits require the last-read version and affect future executions only. Reusable
specifications cannot contain an absolute execution deadline or caller-supplied
origin. Optional `max_duration_seconds` gives each execution a fresh time budget
from creation, including waiting and paused time. Its server-derived `deadline_at`
is frozen with execution intent and is not editable. Per-run page limits, traversal bounds and retention remain ordinary request
settings. The schedule's stop timestamp does not cancel existing work.

A schedule has exactly one cadence:

- Interval: 60–31,536,000 seconds, anchored at the inclusive start timestamp.
- Cron: numeric five-field cron (minute, hour, day, month, weekday), an explicit
  IANA timezone, and standard OR semantics for day-of-month/day-of-week. Lists,
  ranges and steps are supported. Random, hashed, seconds and year forms are not.
  Nonexistent local times at spring DST transitions are skipped. Repeated local
  times at autumn transitions have two distinct UTC occurrences, still subject to
  overlap suppression. Occurrence search is bounded to eight years.

Start is inclusive; optional stop is exclusive. The optional maximum count counts
requests created, including failed or cancelled executions. Editing or pausing a
schedule never resets the counter. Raising the limit can allow more executions.
The UI accepts start/stop in the browser's local timezone and labels that timezone;
cron evaluation uses the independently specified IANA timezone.

Crawler replicas evaluate schedules once per second, using the existing control
transaction lock. Up to 20 due schedules are evaluated per pass. Creating a request,
writing its lineage outbox, updating the execution count and moving the next tick
commit together. No database lock spans network or DuckLake work. A normal five-second
polling tolerance allows dispatch jitter; older ticks are skipped after downtime,
with no catch-up burst. The following also skip a tick without increasing the count:

- The previous schedule execution is still active or paused.
- The global crawler is paused.
- Current request admission is at capacity.

Skipped work resumes only on a future cadence tick. A missing previous current
request means it has retired; cleanup only removes settled requests. Global crawl
allowances and domain politeness remain authoritative when new requests execute.
A created request independently reevaluates seed SQL through the isolated query
service and freezes its result through the existing selection checkpoint.

Postgres owns only editable `request_definitions` and `request_schedules` control
state, including the count, next tick, latest request identity and latest tick
result. There is no schedule-execution history table or new queue. Durable request
intent and outcomes continue to belong to DuckLake.

## Replacing background exploration

The separate background selection loop, historical-check table, acquisition
ownership fields, dispatch allocation and background budgets are removed. Only
request interests authorize new acquisitions. Navigation packages are retained
only for ordinary request follow selection and its existing cleanup gates.
Background-specific evidence is removed. Every acquisition cause names an ordinary
request, including scheduled system requests.

Expansion can be expressed as bounded seed SQL selecting discovered public link
targets that have no observation, followed by depth-zero capture. Seed queries must
explicitly choose ordering, limits and failure semantics. Shared frontier admission
still deduplicates concurrent compatible work. No exploration schedule is enabled
automatically. Processing and materialization lag can make a subsequent run see an
older catalogue; the ordinary acquisition reuse and sharing rules still apply.

## Deployment

Stop old crawler and janitor processes before applying Alembic through revision
`20260908_0005`, then restart the API, crawler and janitor with the new code. The
migration refuses to drop background ownership while queued, retrying or dispatched
background acquisitions exist; drain those before proceeding. It does not delete
crawl evidence. There is no new service, environment variable or NATS topology.
The scheduler runs in every ordinary crawler replica and uses the same Postgres
control authority. `croniter` is installed through the core package lockfile.
