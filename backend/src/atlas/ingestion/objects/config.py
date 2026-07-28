"""Environment-backed raw repository configuration."""

from __future__ import annotations

from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from atlas.platform.config import get_optional, get_path, get_str

from atlas.ingestion.objects.exceptions import RepositoryConfigError
from atlas.ingestion.objects.store import FileObjectStore, ObjectStore, S3ObjectStore

_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / ".atlas" / "repository"


def object_store_from_env(*, maximum_concurrency: int = 1) -> ObjectStore:
    if maximum_concurrency < 1:
        raise ValueError("object-store concurrency must be positive")
    kind = get_str("ATLAS_REPOSITORY_STORAGE").lower()
    if kind == "disk":
        root = get_path("ATLAS_REPOSITORY_ROOT")
        return FileObjectStore(root)
    if kind == "s3":
        client, bucket = _s3_client_from_env(
            maximum_concurrency=maximum_concurrency
        )
        return S3ObjectStore(
            client,
            bucket=bucket,
            prefix=(get_optional("ATLAS_REPOSITORY_S3_PREFIX") or "").strip("/"),
        )
    raise RepositoryConfigError("ATLAS_REPOSITORY_STORAGE must be one of: disk, s3")


def ensure_s3_bucket_from_env() -> str:
    """Create the configured bucket when absent; intended for explicit deployment init jobs."""

    if get_str("ATLAS_REPOSITORY_STORAGE").lower() != "s3":
        raise RepositoryConfigError("S3 bucket initialization requires ATLAS_REPOSITORY_STORAGE=s3")
    client, bucket = _s3_client_from_env()
    try:
        client.head_bucket(Bucket=bucket)
        exists = True
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if status != 404 and code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        exists = False

    if not exists:
        region = _optional("ATLAS_REPOSITORY_S3_REGION") or _optional("AWS_REGION")
        options: dict[str, object] = {"Bucket": bucket}
        if region and region != "us-east-1":
            options["CreateBucketConfiguration"] = {"LocationConstraint": region}
        try:
            client.create_bucket(**options)
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if status not in {409, 412} and code not in {
                "BucketAlreadyExists",
                "BucketAlreadyOwnedByYou",
            }:
                raise
            client.head_bucket(Bucket=bucket)
    return bucket


def _s3_client_from_env(*, maximum_concurrency: int = 1) -> tuple[object, str]:
    bucket = _required("ATLAS_REPOSITORY_S3_BUCKET")
    client_options: dict[str, object] = {
        "endpoint_url": _optional("ATLAS_REPOSITORY_S3_ENDPOINT"),
        "region_name": _optional("ATLAS_REPOSITORY_S3_REGION") or _optional("AWS_REGION"),
        "aws_access_key_id": _optional("ATLAS_REPOSITORY_S3_KEY_ID")
        or _optional("AWS_ACCESS_KEY_ID"),
        "aws_secret_access_key": _optional("ATLAS_REPOSITORY_S3_SECRET_ACCESS_KEY")
        or _optional("AWS_SECRET_ACCESS_KEY"),
        "aws_session_token": _optional("ATLAS_REPOSITORY_S3_SESSION_TOKEN")
        or _optional("AWS_SESSION_TOKEN"),
    }
    use_ssl = _optional_bool("ATLAS_REPOSITORY_S3_USE_SSL")
    if use_ssl is not None:
        client_options["use_ssl"] = use_ssl
    config_options: dict[str, object] = {
        "max_pool_connections": maximum_concurrency,
    }
    url_style = _optional("ATLAS_REPOSITORY_S3_URL_STYLE")
    if url_style is not None:
        if url_style not in {"auto", "path", "virtual"}:
            raise RepositoryConfigError(
                "ATLAS_REPOSITORY_S3_URL_STYLE must be one of: auto, path, virtual"
            )
        config_options["s3"] = {"addressing_style": url_style}
    client_options["config"] = Config(**config_options)
    return boto3.client("s3", **client_options), bucket


def _required(name: str) -> str:
    value = _optional(name)
    if value is None:
        raise RepositoryConfigError(f"{name} is required")
    return value


def _optional(name: str) -> str | None:
    return get_optional(name)


def _optional_bool(name: str) -> bool | None:
    value = _optional(name)
    if value is None:
        return None
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "off"}:
        return False
    raise RepositoryConfigError(f"{name} must be a boolean")
