"""Recover publication claims after lost acknowledgements or abandoned ingestion."""
from datetime import UTC, datetime, timedelta
from itertools import islice
from uuid import UUID

from periplus.ingestion.objects.publication import PREFIX, release
from periplus.ingestion.objects.html import html_object_key
from periplus.ingestion.objects.document import document_object_key
from periplus.platform.catalogue import catalogue_from_env
from periplus.platform.config import get_int
from periplus.platform.messaging.leases import operation_leases, OperationLeaseLost
from periplus.retention.identities import write_claims, retire, retired
from periplus.retention import store as retirement_store
from periplus.retention.runtime import bounded_call, current_roots


async def cleanup_publications(settings, sessions, objects, leases, iterator=None):
    if settings.mode != 'purge':
        return iterator
    iterator = iterator if iterator is not None else iter(objects.list_objects(PREFIX))
    # The iterator is consumed off the event loop; S3 listing is blocking I/O.
    candidates = await bounded_call(lambda: tuple(islice(iterator, settings.batch_size)))
    now = datetime.now(UTC)
    # Leave the complete delivery/recovery horizon plus a bounded worker timeout.
    horizon = max(get_int('PERIPLUS_DEAD_LETTER_TTL_SECONDS'),
                  get_int('PERIPLUS_INGEST_RESULT_TTL_SECONDS'), settings.minimum_age_seconds) + 3600
    for item in candidates:
        parts = item.key.split('/')
        if len(parts) != 4 or item.last_modified > now - timedelta(seconds=horizon):
            continue
        content_hash = parts[2]
        try:
            identity = UUID(parts[3])
            if len(content_hash) != 64 or any(c not in '0123456789abcdef' for c in content_hash):
                continue
        except ValueError:
            continue
        async with operation_leases(leases, [f'observation:{identity}', f'content:{content_hash}'], phase='ingestion', acquire_timeout=0) as guard:
            def cleanup():
                if guard.lost:
                    raise OperationLeaseLost('publication cleanup lost ownership')
                roots, _ = current_roots(sessions, [identity], [])
                if roots:
                    return
                with write_claims({'observation': [str(identity)], 'content': [content_hash]}, allow_retired=True), catalogue_from_env(threads=1, memory_limit='512MB') as catalogue:
                    connection = catalogue.trusted_connection
                    durable = connection.execute('SELECT 1 FROM ingest.visits WHERE visit_id=? LIMIT 1', [identity]).fetchall()
                    if not durable and not retired('observation', str(identity)):
                        # An unfinished request with late evidence still protects
                        # an uploaded object. Ambiguous lineage is retained.
                        if connection.execute('SELECT 1 FROM ingest.fulfillments WHERE observation_id=? LIMIT 1', [identity]).fetchall():
                            return
                        keys = [(key, objects.size(key)) for key in (html_object_key(content_hash), document_object_key(content_hash)) if objects.exists(key)]
                        retire('observation', str(identity), now)
                        for key, size in keys:
                            retirement_store.enqueue(content_hash, key, size, now)
                    if guard.lost:
                        raise OperationLeaseLost('publication cleanup lost ownership')
                    release(objects, content_hash, identity)
            await bounded_call(cleanup)
    return iterator if len(candidates) == settings.batch_size else None
