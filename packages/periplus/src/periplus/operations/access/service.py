from datetime import UTC, datetime
import math
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from periplus.crawl.control.collections.schemas import CollectionSpec
from fastapi import HTTPException
from periplus.operations.access.models import PublicAccessRecord
from periplus.operations.access.schemas import AccessPolicy, AccessView, Capability

class AccessDenied(HTTPException):
    def __init__(self, code: str, detail: str, status: int = 403, retry: int | None = None):
        super().__init__(status, {"code": code, "detail": detail}, headers={"Retry-After": str(retry)} if retry else None)

class AccessStore:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def read(self) -> AccessView:
        with self.sessions() as session:
            row = session.get(PublicAccessRecord, 1)
            if row is None:
                raise AccessDenied("access_unavailable", "Public access settings are unavailable.", 503)
            return AccessView(**row.configuration, version=row.version)

    def save(self, policy: AccessPolicy, expected_version: int) -> AccessView:
        with self.sessions.begin() as session:
            row = session.get(PublicAccessRecord, 1, with_for_update=True)
            if row is None:
                raise AccessDenied("access_unavailable", "Public access settings are unavailable.", 503)
            if row.version != expected_version:
                raise AccessDenied("version_conflict", "Access settings changed. Reload before saving.", 409)
            row.configuration = policy.model_dump(mode="json")
            row.version += 1
            # Do not reset consumed capacity when an operator edits the policy.
            return AccessView(**row.configuration, version=row.version)

    def admit(self, capability: Capability, *, specification: CollectionSpec | None = None, consume: bool = True, now: datetime | None = None) -> None:
        with self.sessions.begin() as session:
            row = session.get(PublicAccessRecord, 1, with_for_update=True)
            if row is None:
                raise AccessDenied("access_unavailable", "Public access settings are unavailable.", 503)
            policy = AccessPolicy.model_validate(row.configuration)
            rule = getattr(policy, capability)
            if not rule.enabled:
                raise AccessDenied("feature_disabled", f"Public {capability} is currently disabled.")
            if specification is not None:
                if (specification.page_limit not in policy.crawl.page_budgets
                        or specification.max_depth not in policy.crawl.max_depths
                        or specification.retention_seconds not in policy.crawl.retention_seconds):
                    raise AccessDenied("options_changed", "Public crawl options changed. Refresh and choose supported options.", 422)
            if not consume:
                return
            stamp = now or (session.scalar(select(func.clock_timestamp())) if session.bind.dialect.name == "postgresql" else datetime.now(UTC))
            stamp = stamp.timestamp()
            windows = dict(row.windows)
            current = windows.get(capability, {"start": stamp, "count": 0})
            if stamp >= current["start"] + rule.window_seconds:
                current = {"start": stamp, "count": 0}
            if current["count"] >= rule.requests:
                raise AccessDenied("rate_limited", f"Public {capability} rate limit reached. Please retry shortly.", 429,
                                   max(1, math.ceil(current["start"] + rule.window_seconds - stamp)))
            windows[capability] = {"start": current["start"], "count": current["count"] + 1}
            row.windows = windows
