"""Freeze bounded SQL selections and incrementally admit their URL interests."""
from collections.abc import Callable
from hashlib import sha256
from datetime import UTC, datetime
from uuid import UUID

from periplus.query.service import QueryRequest

from periplus.crawl.control.collections.exclusions import UrlExcluded
from periplus.crawl.control.collections.discovery import DiscoveryState
from periplus.crawl.control.collections.schemas import CollectionExecutionSpec, CollectionSpec, SelectionContext
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from periplus.crawl.control.collections.scopes import within_allowed_sections
from periplus.crawl.runtime.frontier_store import AdmissionDeferred, CollectionUnavailable, FrontierStore
from periplus.crawl.runtime.navigation import load_navigation_package
from periplus.crawl.runtime.navigation_contract import NavigationPackage
from periplus.crawl.runtime.selection_contract import SelectionCheckpoint
from periplus.crawl.runtime.selection_sql import select_links
from periplus.ingestion.objects.store import ObjectStore

PolicyResolver = Callable[[str], EffectivePolicySnapshot]


def process_seed_selection(store: FrontierStore, collection_id: UUID, policy: PolicyResolver, *,
                           seed_query: Callable[[QueryRequest], SelectionCheckpoint] | None = None) -> str:
    collection = store.get_collection(collection_id)
    if collection is None:
        raise KeyError(collection_id)
    if collection.seeds_settled or collection.status == "settled":
        return "settled"
    spec = CollectionExecutionSpec.model_validate(collection.spec)
    if collection.deadline_at is not None and collection.deadline_at <= datetime.now(UTC):
        store.stop_collection(collection.id, reason="duration_limit")
        return "settled"
    if collection.status == "paused":
        return "waiting"
    if collection.selection_checkpoint is None:
        urls, snapshot, query_id, selected_at = spec.seed_urls, None, None, datetime.now(UTC)
        if spec.seed_description:
            discovery = DiscoveryState.model_validate(collection.discovery_state or {})
            if not discovery.complete:
                return "waiting"
            urls = (*urls, *discovery.urls)
        if spec.seed_sql:
            if seed_query is None:
                raise RuntimeError("corpus seed query service is unavailable")
            selected = seed_query(QueryRequest(sql=spec.seed_sql, parameters=list(spec.seed_parameters)))
            urls = (*urls, *selected.urls)
            snapshot = selected.source_snapshot
            query_id, selected_at = selected.source_query_id, selected.selected_at
        checkpoint = store.freeze_selection(collection_id, SelectionCheckpoint(
            urls=tuple(url for url in urls if within_allowed_sections(url, list(spec.allowed_sections))),
            source_snapshot=snapshot, source_query_id=query_id, selected_at=selected_at,
        ), seeds=True)
    else:
        checkpoint = SelectionCheckpoint.model_validate(collection.selection_checkpoint)
    return _resume(store, collection_id, collection_id, checkpoint, SelectionContext(
        depth=0, rule_id="seeds",
    ), policy, seeds=True)


def process_link_selection(store: FrontierStore, interest_id: UUID, policy: PolicyResolver,
                           objects: ObjectStore) -> str:
    interest = store.get_interest(interest_id)
    if interest is None:
        raise KeyError(interest_id)
    if interest.status in ("settled", "cancelled"):
        return "settled"
    if interest.status != "selecting":
        return "waiting"
    collection = store.get_collection(interest.collection_id)
    if collection is None:
        raise KeyError(interest.collection_id)
    spec = CollectionExecutionSpec.model_validate(collection.spec)
    if collection.deadline_at is not None and collection.deadline_at <= datetime.now(UTC):
        store.stop_collection(collection.id, reason="duration_limit")
        return "settled"
    if collection.status == "paused":
        return "waiting"
    context = SelectionContext.model_validate(interest.context)
    if interest.selection_checkpoint is None:
        acquisition = store.get_acquisition(interest.acquisition_id)
        assert acquisition is not None
        urls, source = (), None
        if context.depth < spec.max_depth and acquisition.navigation is not None:
            package = NavigationPackage.model_validate(acquisition.navigation)
            urls = select_links(spec.follow_sql, load_navigation_package(objects, package))
            source = f"navigation:{package.sha256}"
        checkpoint = store.freeze_selection(interest_id, SelectionCheckpoint(
            urls=tuple(url for url in urls if within_allowed_sections(url, list(spec.allowed_sections))),
            source_snapshot=source, selected_at=datetime.now(UTC),
        ), seeds=False)
    else:
        checkpoint = SelectionCheckpoint.model_validate(interest.selection_checkpoint)
    return _resume(store, interest_id, interest.collection_id, checkpoint, SelectionContext(
        depth=context.depth + 1, parent_observation_id=interest.acquisition_id,
        rule_id="follow:" + sha256(spec.follow_sql.encode()).hexdigest(),
    ), policy, seeds=False)


def _resume(store: FrontierStore, identity: UUID, collection_id: UUID,
            checkpoint: SelectionCheckpoint | None, context: SelectionContext,
            policy: PolicyResolver, *, seeds: bool) -> str:
    if checkpoint is None:
        return "settled"
    finish = store.finish_seed_selection if seeds else store.finish_link_selection
    stop = min(len(checkpoint.urls), checkpoint.cursor + 64)
    for cursor in range(checkpoint.cursor, stop):
        collection = store.get_collection(collection_id)
        assert collection is not None
        spec = CollectionExecutionSpec.model_validate(collection.spec)
        if collection.deadline_at is not None and collection.deadline_at <= datetime.now(UTC):
            store.stop_collection(collection_id, reason="duration_limit")
            return "settled"
        if collection.status != "active":
            return "waiting" if collection.status == "paused" else "settled"
        if collection.reserved + collection.consumed >= collection.page_limit:
            finish(identity)
            store.settle_collection(collection_id)
            return "settled"
        url = checkpoint.urls[cursor]
        try:
            store.admit(collection_id, url, context, policy(url))
        except UrlExcluded:
            if not store.advance_selection(identity, cursor, seeds=seeds, excluded=True):
                return "waiting"
            continue
        except AdmissionDeferred:
            store.set_waiting_reason(collection_id, "frontier_admission_capacity")
            return "waiting"
        except CollectionUnavailable:
            # Cancellation/pause/budget can race policy resolution and admission.
            # Leave the cursor on this URL so the next pass inspects current state.
            return "waiting"
        store.advance_selection(identity, cursor, seeds=seeds)
    store.set_waiting_reason(collection_id, None)
    if stop == len(checkpoint.urls):
        finish(identity)
        store.settle_collection(collection_id)
        return "settled"
    return "progress"
