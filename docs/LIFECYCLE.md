# Capture and materialization lifecycle

1. Postgres admits collection-local URL interests into the shared frontier.
2. A crawler obtains a per-domain permit and acquires one page through standard CDP.
3. It stores immutable payload bytes. The acquisition result and its collection
   associations are frozen transactionally with an outbox entry in Postgres.
4. The outbox relay verifies the bytes, commits the standalone capture envelope
   and its raw journal event, then sends a NATS hint. It verifies the returned
   capture identity/digest before marking Postgres result associations archived.
5. Materializer planners compare each build's bounded cursors with raw journal
   heads. Lost notifications cannot conceal committed archive events.
6. Any worker of the matching recipe claims a bounded batch, parses required
   documents, writes and verifies ClickHouse output, and commits a contiguous
   Postgres checkpoint before acknowledging delivery.
7. The public query API reads one publication binding for the request. Hidden
   candidates are visible only after explicit verified activation.

An archive commit can survive a lost response. Replay may encounter duplicate
journal references, but capture identities and envelope digests must agree.
ClickHouse writes can also survive lost acknowledgements. Exact claims outlive
bounded writers; after expiry another worker verifies existing output before
inserting anything missing. Permanent input errors remain inspectable on their
batch and block publication. Other shards and live work can continue.

Archive imports enter at step 3. Their Postgres job records explain why an import
was requested and which source range remains; archived envelopes explain the
result independently. A retained archive never requires re-downloading Common
Crawl to rebuild the corpus.

The janitor reclaims transient navigation/probe objects, completed frontier
execution, expired write claims and private query history. It applies explicitly
requested capture tombstones. Materializer coordinators drop drained ClickHouse
targets. See [RETENTION.md](RETENTION.md) for the current raw-deletion boundary.
