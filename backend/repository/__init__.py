"""Atlas repository storage boundary."""

from repository.config import ensure_s3_bucket_from_env, object_store_from_env, staging_root_from_env
from repository.exceptions import (
    RepositoryConfigError,
    RepositoryError,
    RepositoryIntegrityError,
    RepositoryKeyError,
    RepositoryObjectNotFound,
)
from repository.html import (
    HtmlIdentity,
    RawHtmlRepository,
    StoredHtml,
    html_object_key,
    identify_html,
)
from repository.object_store import FileObjectStore, ObjectStore, S3ObjectStore
from repository.pipeline import IngestionWorkerConfig, RepositoryPipeline
from repository.service import (
    PreparedIngestion,
    RepositoryCacheHit,
    RepositoryIngestor,
    RepositoryLimits,
    repository_ingestor_from_env,
)

__all__ = [
    "FileObjectStore",
    "HtmlIdentity",
    "ObjectStore",
    "IngestionWorkerConfig",
    "PreparedIngestion",
    "RawHtmlRepository",
    "RepositoryConfigError",
    "RepositoryError",
    "RepositoryIntegrityError",
    "RepositoryKeyError",
    "RepositoryObjectNotFound",
    "RepositoryIngestor",
    "RepositoryLimits",
    "RepositoryPipeline",
    "RepositoryCacheHit",
    "S3ObjectStore",
    "StoredHtml",
    "html_object_key",
    "identify_html",
    "ensure_s3_bucket_from_env",
    "object_store_from_env",
    "repository_ingestor_from_env",
    "staging_root_from_env",
]
