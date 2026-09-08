"""Explicit policy for synthetic native observation fixtures."""
from uuid import UUID

from periplus.crawl.control.content_policies.schemas import ContentPolicySnapshot


def capture_policy() -> ContentPolicySnapshot:
    return ContentPolicySnapshot(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        slug="test", scheme="*", host="*", path_prefix="/", path_mode="prefix",
    )
