"""Assemble known and uncertain acquisition history without collection ownership."""
from datetime import UTC, datetime
from periplus.crawl.acquisition.context import AcquisitionContext
from periplus.crawl.acquisition.models import AcquisitionAttemptEvidence, AcquisitionResult
from periplus.crawl.acquisition.evidence import attempt_records, step_records
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from periplus.platform.catalogue.records import VisitEvidence, VisitRecord


def acquisition_context(acquisition) -> AcquisitionContext:
    previous = [AcquisitionResult.model_validate(value) for value in acquisition.prior_results]
    attempts = [result.attempt_evidence for result in previous if result.attempt_evidence is not None]
    for uncertain in acquisition.uncertain_attempts:
        attempts.append(AcquisitionAttemptEvidence(
            attempt=0, started_at=uncertain["started_at"], completed_at=None,
            requested_url=acquisition.url, outcome="uncertain", resource_usage=uncertain["resource_usage"],
        ))
    attempts.sort(key=lambda attempt: attempt.started_at)
    attempts = tuple(attempt.model_copy(update={"attempt": index + 1}) for index, attempt in enumerate(attempts))
    admitted = acquisition.created_at
    if admitted.tzinfo is None:
        admitted = admitted.replace(tzinfo=UTC)
    policy = EffectivePolicySnapshot.model_validate(acquisition.requirements)
    if acquisition.attempt_domain_policy is not None:
        from periplus.crawl.control.domain_policies.schemas import DomainPolicySnapshot
        policy = policy.model_copy(update={"domain": DomainPolicySnapshot.model_validate(acquisition.attempt_domain_policy)})
    return AcquisitionContext(
        acquisition_id=acquisition.id, admitted_at=admitted, visibility=acquisition.visibility,
        policy=policy,
        attempt_reserved_ms=acquisition.attempt_reserved_ms,
        dispatch_policy_version=acquisition.dispatch_policy_version,
        exclusions=tuple(acquisition.attempt_exclusions),
        exclusion_policy_version=acquisition.attempt_exclusion_version,
        prior_attempts=attempts, prior_steps=tuple(step for result in previous for step in result.steps),
    )


def terminal_evidence(acquisition, now: datetime, outcome: str) -> VisitEvidence:
    if outcome not in {"failed", "cancelled"}:
        raise ValueError("terminal evidence requires failed or cancelled outcome")
    context = acquisition_context(acquisition)
    attempts = attempt_records(acquisition.id, context.prior_attempts)
    return VisitEvidence(visit=VisitRecord(
        visit_id=acquisition.id, requested_url=acquisition.url, visibility=context.visibility,
        admitted_at=context.admitted_at, started_at=attempts[0].started_at if attempts else None,
        finished_at=now, outcome=outcome,
    ), attempts=attempts, steps=step_records(acquisition.id, context.prior_steps))
