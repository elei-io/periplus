"""Atlas repository storage boundary."""

from repository.exceptions import (
    RepositoryIntegrityError,
    RepositoryKeyError,
    RepositoryObjectNotFound,
)
from repository.objects.html import (
    RawHtmlRepository,
    identify_html,
)
from repository.objects.artifact import (
    ArtifactIdentity,
    RawArtifactRepository,
    StoredArtifact,
    artifact_object_key,
)
from repository.objects.store import FileObjectStore, S3ObjectStore
from repository.ingestion.pipeline import RepositoryPipeline
from repository.service import (
    RepositoryCacheHit,
    RepositoryIngestor,
    repository_ingestor_from_env,
)

__all__ = [
    "FileObjectStore",
    "ArtifactIdentity",
    "RawArtifactRepository",
    "RawHtmlRepository",
    "RepositoryIntegrityError",
    "RepositoryKeyError",
    "RepositoryObjectNotFound",
    "RepositoryIngestor",
    "RepositoryPipeline",
    "RepositoryCacheHit",
    "S3ObjectStore",
    "StoredArtifact",
    "artifact_object_key",
    "identify_html",
    "repository_ingestor_from_env",
]
