from uuid import uuid4

from periplus.crawl.runtime.navigation_contract import NavigationPackage


def navigation_package() -> NavigationPackage:
    return NavigationPackage(
        object_name="runtime/navigation/test.arrow",
        sha256="0" * 64,
        schema_version=1,
        recipe="recipe",
        row_count=1,
        byte_size=10,
    )


def policy_snapshot(_variant: str = "default") -> dict:
    return {
        "content": {
            "id": str(uuid4()),
            "slug": "content-policy-test",
            "scheme": "*",
            "host": "*",
            "path_prefix": "/",
            "path_mode": "prefix",
            "content": {},
        },
        "domain": {
            "id": str(uuid4()),
            "slug": "domain-policy-test",
            "host_match": "*",
            "maximum_concurrency": 4,
            "minimum_request_interval_seconds": 0,
        },
    }
