"""Public crawler reads and versioned operator controls."""
from uuid import UUID

from ._http import request
from .types import (
    AcquisitionView, ObservationLineagePage, LiveView, DomainPolicyCreateRequest, DomainPolicyListResponse, DomainPolicyRecord,
    DomainPolicyUpdateRequest, FrontierControlView, FrontierSettings,
)


async def controls() -> FrontierControlView:
    return FrontierControlView.model_validate(await request("GET", "/frontier/controls"))


async def replace_controls(settings: FrontierSettings, *, expected_version: int) -> FrontierControlView:
    if expected_version < 1:
        raise ValueError("expected_version must be positive")
    return FrontierControlView.model_validate(await request("PUT", "/frontier/controls", json={
        "settings": settings.model_dump(mode="json"), "expected_version": expected_version,
    }))


async def domains(*, limit: int = 100, offset: int = 0, match_pattern: str | None = None,
                  enabled: bool | None = None) -> DomainPolicyListResponse:
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("domain page outside bounds")
    params = {"limit": limit, "offset": offset}
    if match_pattern is not None:
        params["match_pattern"] = match_pattern
    if enabled is not None:
        params["enabled"] = enabled
    return DomainPolicyListResponse.model_validate(await request("GET", "/domain-policies/", params=params))


async def create_domain(policy: DomainPolicyCreateRequest) -> DomainPolicyRecord:
    return DomainPolicyRecord.model_validate(await request("POST", "/domain-policies/", json=policy.model_dump(mode="json")))


async def update_domain(id: UUID | str, changes: DomainPolicyUpdateRequest) -> DomainPolicyRecord:
    return DomainPolicyRecord.model_validate(await request("PATCH", f"/domain-policies/{UUID(str(id))}",
                                                         json=changes.model_dump(mode="json", exclude_unset=True)))


async def delete_domain(id: UUID | str, *, expected_version: int) -> None:
    if expected_version < 1:
        raise ValueError("expected_version must be positive")
    await request("DELETE", f"/domain-policies/{UUID(str(id))}", params={"expected_version": expected_version})


async def item(id: UUID | str) -> AcquisitionView:
    identity = UUID(str(id))
    value = AcquisitionView.model_validate(await request("GET", f"/frontier/items/{identity}"))
    if value.id != identity:
        raise ValueError("frontier response changed acquisition identity")
    return value


async def live() -> LiveView:
    """Public activity; missing history remains unknown rather than zero."""
    return LiveView.model_validate(await request("GET", "/frontier/live"))


async def lineage(id: UUID | str, *, limit: int = 20, cursor: str | None = None) -> ObservationLineagePage:
    """Committed capture causes and result users, retained after frontier cleanup.

    Ingestion may add evidence later; refresh the first page to see late commits.
    """
    identity = UUID(str(id))
    if not 1 <= limit <= 100 or (cursor is not None and len(cursor) > 512):
        raise ValueError("lineage page outside bounds")
    params = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    value = ObservationLineagePage.model_validate(await request(
        "GET", f"/frontier/observations/{identity}/lineage", params=params))
    if value.observation_id != identity:
        raise ValueError("lineage response changed observation identity")
    return value
