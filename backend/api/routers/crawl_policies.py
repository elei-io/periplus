from typing import Annotated
from uuid import UUID

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.catalogue_pool import CatalogueReadPool, CatalogueReadPoolExhausted
from config import get_float, get_int
from control.crawl_policies.models import CrawlPolicy, CrawlProfile
from control.crawl_policies.schemas import (
    CrawlPolicyCreateRequest,
    CrawlPolicyListResponse,
    CrawlPolicyRecord,
    CrawlPolicyUpdateRequest,
    CrawlProfileCreateRequest,
    CrawlProfileListResponse,
    CrawlProfileRecord,
    CrawlProfileUpdateRequest,
    PolicyTrialApplyRequest,
    PolicyTrialComparisonRecord,
    PolicyTrialReportResponse,
    PolicyTrialSummaryRecord,
)
from control.crawl_policies.service import (
    apply_policy_trial_candidate,
    count_crawl_policies,
    count_crawl_profiles,
    create_crawl_policy,
    create_crawl_profile,
    delete_crawl_policy,
    find_crawl_policies_for_urls,
    get_crawl_policy,
    get_crawl_profile,
    get_crawl_profile_by_slug,
    list_crawl_policies,
    list_crawl_profiles,
    match_for_policy,
    profile_config_hash,
    update_crawl_policy,
    update_crawl_profile,
)
from db.session import get_session
from repository.catalogue.policy_trials import (
    PolicyTrialComparison,
    get_policy_trial_application_domain,
    get_policy_trial_report,
)

router = APIRouter(prefix="/crawl-policies", tags=["crawl-policies"])
profile_router = APIRouter(prefix="/crawl-profiles", tags=["crawl-profiles"])


def _profile_record(profile: CrawlProfile) -> CrawlProfileRecord:
    return CrawlProfileRecord.model_validate(profile)


def _record(policy: CrawlPolicy) -> CrawlPolicyRecord:
    return CrawlPolicyRecord(
        id=policy.id,
        slug=policy.slug,
        scheme=policy.scheme,
        host=policy.host,
        path_prefix=policy.path_prefix,
        path_mode=policy.path_mode,
        match=match_for_policy(policy),
        profile=_profile_record(policy.profile),
        max_concurrency=policy.max_concurrency,
        enabled=policy.enabled,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def _trial_comparison_record(
    item: PolicyTrialComparison, current: CrawlPolicy | None
) -> PolicyTrialComparisonRecord:
    current_profile = current.profile.slug if current is not None else "unknown"
    return PolicyTrialComparisonRecord(
        **item.__dict__,
        current_policy_id=current.id if current is not None else None,
        current_profile=current_profile,
        applied=(
            current is not None
            and current_profile == item.candidate_profile
            and profile_config_hash(current.profile.config)
            == item.candidate_profile_definition_hash
        ),
    )


@profile_router.get("/", response_model=CrawlProfileListResponse)
def list_profiles(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CrawlProfileListResponse:
    return CrawlProfileListResponse(
        items=[
            _profile_record(profile)
            for profile in list_crawl_profiles(session, limit=limit, offset=offset)
        ],
        total=count_crawl_profiles(session),
        limit=limit,
        offset=offset,
    )


@profile_router.post("/", response_model=CrawlProfileRecord, status_code=201)
def create_profile(
    payload: CrawlProfileCreateRequest,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlProfileRecord:
    try:
        return _profile_record(create_crawl_profile(session, payload))
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@profile_router.get("/{profile_id}", response_model=CrawlProfileRecord)
def get_profile(
    profile_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlProfileRecord:
    profile = get_crawl_profile(session, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Crawl profile not found.")
    return _profile_record(profile)


@profile_router.patch("/{profile_id}", response_model=CrawlProfileRecord)
def update_profile(
    profile_id: UUID,
    payload: CrawlProfileUpdateRequest,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlProfileRecord:
    profile = get_crawl_profile(session, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Crawl profile not found.")
    try:
        updated = update_crawl_profile(
            session,
            profile=profile,
            description_set="description" in payload.model_fields_set,
            **payload.model_dump(exclude_unset=True),
        )
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _profile_record(updated)


@router.get("/", response_model=CrawlPolicyListResponse)
def list_(
    session: Annotated[Session, Depends(get_session)],
    match_pattern: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    profile_slug: Annotated[str | None, Query()] = None,
    transport: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CrawlPolicyListResponse:
    filters = {
        "match_pattern": match_pattern,
        "enabled": enabled,
        "profile_slug": profile_slug,
        "transport": transport,
    }
    return CrawlPolicyListResponse(
        items=list_crawl_policies(session, **filters, limit=limit, offset=offset),
        total=count_crawl_policies(session, **filters),
        limit=limit,
        offset=offset,
    )


@router.post("/", response_model=CrawlPolicyRecord, status_code=201)
def create_policy(
    payload: CrawlPolicyCreateRequest,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlPolicyRecord:
    try:
        return _record(create_crawl_policy(session, payload))
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/trials", response_model=PolicyTrialReportResponse)
def trials(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PolicyTrialReportResponse:
    pool: CatalogueReadPool = request.app.state.catalogue_read_pool
    try:
        catalogue = pool.acquire()
    except CatalogueReadPoolExhausted as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        report = get_policy_trial_report(catalogue, limit=limit, offset=offset)
    except duckdb.Error as exc:
        raise HTTPException(
            status_code=503, detail="Policy trial evidence is temporarily unavailable."
        ) from exc
    finally:
        pool.release(catalogue)

    configured_rate = get_float("ATLAS_POLICY_TRIAL_SAMPLE_SHARE", exclusive=False)
    origins = [_origin_url(item.scheme, item.host, item.port) for item in report.comparisons]
    current_policies = find_crawl_policies_for_urls(session, urls=origins)
    return PolicyTrialReportResponse(
        summary=PolicyTrialSummaryRecord(
            sampling_active=configured_rate > 0,
            configured_sample_rate=configured_rate,
            observed_sample_rate=report.summary.observed_sample_rate,
            max_in_flight=get_int("ATLAS_POLICY_TRIAL_MAX_IN_FLIGHT"),
            use_crawls=report.summary.use_crawls,
            selected_trials=report.summary.selected_trials,
            sample_crawls=report.summary.sample_crawls,
            completed_pairs=report.summary.completed_pairs,
            awaiting_samples=report.summary.awaiting_samples,
            pairs_with_failure=report.summary.pairs_with_failure,
            last_trial_at=report.summary.last_trial_at,
        ),
        items=[
            _trial_comparison_record(item, current_policies[origin])
            for item, origin in zip(report.comparisons, origins, strict=True)
        ],
        total=report.total_comparisons,
        limit=limit,
        offset=offset,
    )


@router.post("/trials/apply", response_model=CrawlPolicyRecord)
def apply_trial(
    payload: PolicyTrialApplyRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlPolicyRecord:
    pool: CatalogueReadPool = request.app.state.catalogue_read_pool
    try:
        catalogue = pool.acquire()
    except CatalogueReadPoolExhausted as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        profile = get_crawl_profile_by_slug(session, payload.profile)
        if profile is None:
            raise HTTPException(status_code=422, detail="Crawl profile not found.")
        supported = get_policy_trial_application_domain(
            catalogue,
            scheme=payload.scheme,
            host=payload.host,
            port=payload.port,
            profile_slug=payload.profile,
            profile_config_hash=profile_config_hash(profile.config),
        )
    finally:
        pool.release(catalogue)
    if supported is None:
        raise HTTPException(
            status_code=422,
            detail="No completed policy trial supports this domain and profile.",
        )
    try:
        policy = apply_policy_trial_candidate(
            session,
            scheme=payload.scheme,
            host=payload.host,
            port=payload.port,
            profile_slug=payload.profile,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _record(policy)


def _origin_url(scheme: str, host: str, port: int) -> str:
    default_port = {"http": 80, "https": 443}[scheme]
    authority = host if port == default_port else f"{host}:{port}"
    return f"{scheme}://{authority}/"


@router.get("/{policy_id}", response_model=CrawlPolicyRecord)
def get(
    policy_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlPolicyRecord:
    policy = get_crawl_policy(session, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")
    return _record(policy)


@router.patch("/{policy_id}", response_model=CrawlPolicyRecord)
def update(
    policy_id: UUID,
    payload: CrawlPolicyUpdateRequest,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlPolicyRecord:
    policy = get_crawl_policy(session, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")
    try:
        updated = update_crawl_policy(
            session,
            policy=policy,
            **payload.model_dump(exclude_unset=True),
        )
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _record(updated)


@router.delete("/{policy_id}", status_code=204)
def delete(
    policy_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    policy = get_crawl_policy(session, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")
    try:
        delete_crawl_policy(session, policy=policy)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
