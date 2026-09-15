# Retention and janitor ownership

Raw capture tombstones are the corpus retirement authority. Postgres contains the
operator request and its execution status, not the only copy of the deletion.
`POST /operations/captures/{capture_id}/retirement` queues a request. Captures still
needed by unfinished or unexpired collections are rejected. Reuse admission
excludes captures with an accepted retirement intent.

The janitor takes exact capture/body claims, writes the immutable tombstone first,
appends its journal notification and marks the request complete. Materializers
remove that capture from each target. Replay always checks the tombstone directly,
including when the notification was lost or the manifest predates retirement.

Current retirement is **logical**. Raw payload bytes and archive envelopes remain
retained. Physical raw garbage collection is deliberately absent: shared bodies
cannot be deleted merely because one capture was retired. A future reclamation
pass needs a proven retained-reference mark set and backup/tombstone policy.
Never point a generic age-based object lifecycle rule at the raw namespace.

The janitor has a small bounded loop:

- Reclaim expired Periplus navigation and probe objects.
- Reclaim completed frontier execution; preserve collections and result associations.
- Remove expired exact write claims and expired private query history.
- Apply queued capture retirement requests.

It does not compact ClickHouse or manage query-target publication. ClickHouse
owns its physical parts; materializer coordinators drop complete cancelled/retired
build databases after the ownership/query drain. The latest previous build stays
available until another candidate replaces it.
