from typing import Annotated
from uuid import UUID

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from api.catalogue_pool import CatalogueReadPool, CatalogueReadPoolExhausted
from config import get_float, get_int
from control.crawl_policies.models import CrawlPolicy
from control.crawl_policies.schemas import (
    CrawlPolicyListResponse,
    CrawlPolicyRecord,
    CrawlPolicyUpdateRequest,
    PolicyTrialApplyRequest,
    PolicyTrialComparisonRecord,
    PolicyTrialReportResponse,
    PolicyTrialSummaryRecord,
)
from control.crawl_policies.service import (
    apply_policy_trial_candidate,
    count_crawl_policies,
    delete_crawl_policy,
    find_crawl_policies_for_urls,
    get_crawl_policy,
    list_crawl_policies,
    match_for_policy,
    update_crawl_policy,
)
from control.crawl_policies.templates import template_for_config
from db.session import get_session
from repository.catalogue.policy_trials import (
    PolicyTrialComparison,
    get_policy_trial_application_domain,
    get_policy_trial_report,
)

router = APIRouter(prefix="/crawl-policies", tags=["crawl-policies"])


def _record(policy) -> CrawlPolicyRecord:
    return CrawlPolicyRecord(
        id=policy.id,
        metric_slug=policy.metric_slug,
        domain_group=policy.domain_group,
        url_match_id=policy.url_match_id,
        match=match_for_policy(policy),
        enabled=policy.enabled,
        config=policy.config or {},
        revision=policy.revision,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def _trial_comparison_record(
    item: PolicyTrialComparison, current: CrawlPolicy | None
) -> PolicyTrialComparisonRecord:
    current_template = template_for_config(
        current.config if current is not None else None
    ).name
    return PolicyTrialComparisonRecord(
        scheme=item.scheme,
        host=item.host,
        port=item.port,
        registrable_domain=item.registrable_domain,
        use_template=item.use_template,
        candidate_template=item.candidate_template,
        selected_trials=item.selected_trials,
        completed_pairs=item.completed_pairs,
        recovered_crawls=item.recovered_crawls,
        sample_failures=item.sample_failures,
        identical_documents=item.identical_documents,
        median_html_delta_percent=item.median_html_delta_percent,
        median_visible_text_delta_percent=item.median_visible_text_delta_percent,
        median_element_delta_percent=item.median_element_delta_percent,
        mean_use_visible_text_chars=item.mean_use_visible_text_chars,
        mean_sample_visible_text_chars=item.mean_sample_visible_text_chars,
        use_visible_text_stddev=item.use_visible_text_stddev,
        sample_visible_text_stddev=item.sample_visible_text_stddev,
        use_visible_text_cv=item.use_visible_text_cv,
        sample_visible_text_cv=item.sample_visible_text_cv,
        use_distinct_document_ratio=item.use_distinct_document_ratio,
        sample_distinct_document_ratio=item.sample_distinct_document_ratio,
        median_use_quality_flag_count=item.median_use_quality_flag_count,
        median_sample_quality_flag_count=item.median_sample_quality_flag_count,
        use_acquisition_failure_count=item.use_acquisition_failure_count,
        sample_acquisition_failure_count=item.sample_acquisition_failure_count,
        median_duration_delta_ms=item.median_duration_delta_ms,
        last_trial_at=item.last_trial_at,
        current_policy_id=current.id if current is not None else None,
        current_template=current_template,
        applied=current_template == item.candidate_template,
        verdict=item.verdict,
        verdict_reason=item.verdict_reason,
    )


@router.get("/", response_model=CrawlPolicyListResponse)
def list_(
    session: Annotated[Session, Depends(get_session)],
    match_pattern: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    template: Annotated[str | None, Query()] = None,
    mode: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CrawlPolicyListResponse:
    return CrawlPolicyListResponse(
        items=list_crawl_policies(
            session=session,
            match_pattern=match_pattern,
            enabled=enabled,
            template=template,
            mode=mode,
            limit=limit,
            offset=offset,
        ),
        total=count_crawl_policies(
            session=session,
            match_pattern=match_pattern,
            enabled=enabled,
            template=template,
            mode=mode,
        ),
        limit=limit,
        offset=offset,
    )


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

    configured_rate = get_float(
        "ATLAS_POLICY_TRIAL_SAMPLE_SHARE", exclusive=False
    )
    origins = [
        _origin_url(item.scheme, item.host, item.port)
        for item in report.comparisons
    ]
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
        registrable_domain = get_policy_trial_application_domain(
            catalogue,
            scheme=payload.scheme,
            host=payload.host,
            port=payload.port,
            template_name=payload.template,
        )
    except duckdb.Error as exc:
        raise HTTPException(
            status_code=503, detail="Policy trial evidence is temporarily unavailable."
        ) from exc
    finally:
        pool.release(catalogue)
    if registrable_domain is None:
        raise HTTPException(
            status_code=422,
            detail="No completed policy trial supports this domain and template.",
        )
    try:
        policy = apply_policy_trial_candidate(
            session,
            scheme=payload.scheme,
            host=payload.host,
            port=payload.port,
            registrable_domain=registrable_domain,
            template_name=payload.template,
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
    policy = get_crawl_policy(session=session, policy_id=policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")

    return _record(policy)


@router.patch("/{policy_id}", response_model=CrawlPolicyRecord)
def update(
    policy_id: UUID,
    request: CrawlPolicyUpdateRequest,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlPolicyRecord:
    policy = get_crawl_policy(session=session, policy_id=policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")

    updated = update_crawl_policy(
        session=session,
        policy=policy,
        enabled=request.enabled,
        match=request.match,
        config=request.config,
        domain_group=request.domain_group,
    )
    return _record(updated)


@router.delete("/{policy_id}", status_code=204)
def delete(
    policy_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    policy = get_crawl_policy(session=session, policy_id=policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Crawl policy not found.")

    delete_crawl_policy(session=session, policy=policy)
