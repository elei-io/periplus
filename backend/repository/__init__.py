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
from repository.objects.document import (
    ExactDocumentIdentity,
    ExactDocumentRepository,
    MediaTypeDetection,
    StoredDocument,
    detect_media_type,
    document_object_key,
)
from repository.objects.store import FileObjectStore, S3ObjectStore
from repository.service import (
    RepositoryIngestor,
    repository_ingestor_from_env,
)

__all__ = [
    "FileObjectStore",
    "ExactDocumentIdentity",
    "ExactDocumentRepository",
    "MediaTypeDetection",
    "RawHtmlRepository",
    "RepositoryIntegrityError",
    "RepositoryKeyError",
    "RepositoryObjectNotFound",
    "RepositoryIngestor",
    "S3ObjectStore",
    "StoredDocument",
    "detect_media_type",
    "document_object_key",
    "identify_html",
    "repository_ingestor_from_env",
]
