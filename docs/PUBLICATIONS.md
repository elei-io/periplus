# Materializations and catalogue event publications

Status: accepted target design.

## Contract

A managed view is either virtual or materialized. A view has at most one active
materialization and a materialization has one immutable incarnation. Atlas does
not revise an active materialization in place: dematerialize, edit the view, and
materialize again.

Materializing creates:

1. a durable, filtered NATS consumer for one declared driving DuckLake table;
2. one private table in `_atlas_materializations`;
3. a full, transactionally consistent initial population; and
4. a public wrapper view that keeps the original view name stable; and
5. one immutable refresh strategy: keyed, append, or full.

The initial NATS consumer is created before the snapshot is read. After
bootstrap, messages at or before that snapshot are acknowledged and subsequent
messages drive refreshes, so there is no observation gap.

An active materialization is either live or paused. Pausing stops pulls and
retains the NATS cursor. Continuing resumes from that cursor. Dematerializing
deletes the NATS consumer, restores the original SQL view, drops the private
table, and archives the control-plane incarnation.

## Catalogue event relay

The catalogue relay owns one catalogue-wide `DMLConsumer(mode="ticks")`. Each
schema-independent DuckLake tick carries all touched table IDs; the relay
resolves their physical identities and publishes one event per table to
`atlas.catalogue.dml.<table_uuid>`. The table UUID is the physical incarnation
fence. A deterministic message ID derived from `(table_uuid, snapshot_id)`
makes publish-before-CDC-commit replay safe.

One catalogue-wide `DDLConsumer(mode="changes")` publishes physical catalogue
changes to `atlas.catalogue.ddl`. These are the relay's only two DuckDB
connections.

No downstream worker opens either DuckLake consumer type. Maintenance uses one
durable wildcard NATS consumer and treats DML ticks as coalesced wake-up hints;
materializations retain their filtered ticks until the target refresh commits.

The DML cursor follows table creation and crosses schema boundaries without
handoff because ticks have no row schema. DDL still causes dependent workers
to enter `blocked_schema` when their driving table changes incompatibly;
Postgres remains the source of desired user intent.

## Refresh semantics

DML ticks intentionally contain no row keys. After coalescing its configured
delay, a materialization worker uses `cdc_dml_changes_query` to read only the
driving table's retained snapshot range. This is a stateless query on the
worker's existing connection, not another persistent DuckLake CDC consumer.

`keyed` is the normal strategy. Its ordered, possibly composite key must exist
in both the driving table and view output. Atlas derives the changed tuples and
transactionally replaces only those result groups:

```sql
BEGIN;
DELETE FROM _atlas_materializations.target
WHERE EXISTS (SELECT 1 FROM _atlas_changed_keys WHERE <key match>);
INSERT INTO _atlas_materializations.target
SELECT * FROM (<stored source SQL>) AS source
WHERE EXISTS (SELECT 1 FROM _atlas_changed_keys WHERE <key match>);
COMMIT;
```

The key is a refresh boundary rather than necessarily a row primary key: one
tuple may replace zero, one, or many output rows.

`append` requires the composite key to uniquely identify each result row. It
accepts inserts only and anti-joins the target by that identity, making NATS
redelivery after a commit idempotent. A source update or delete blocks the
incarnation instead of silently retaining incorrect output.

`full` explicitly deletes and recreates all rows in one transaction. It is the
fallback for global aggregates, ranking, and other queries without a bounded
invalidation key. An omitted key never implicitly selects append behavior.

The target is never dropped or renamed during refresh, so its DuckLake table
UUID remains stable. A schema mismatch rolls the transaction back and blocks
the incarnation. A strategy change requires dematerializing and creating a new
incarnation. Downstream sinks may choose their own coalescing/latency trade-off.

## Ownership

| Store | Authority |
|---|---|
| Postgres | View SQL, driving table identity, desired/observed lifecycle, target identity, refresh delay, diagnostics |
| NATS JetStream | Durable DML/DDL events and each downstream consumer cursor |
| DuckLake | Source tables, stable materialization tables, snapshots, DML/DDL history |

There is no `materialization_coverage` table, definition revision, live/backfill
scope queue, scope fence, staging Arrow hand-off, or materialization dead-letter
stream. The retained NATS message is the retry boundary and a successful
DuckLake refresh precedes its acknowledgement.

Future publications and public sinks consume the same catalogue subjects. They
reference existing data and choose their own delivery, coalescing, and
bootstrap contracts; they do not create another Atlas materialization.
