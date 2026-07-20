"""Shared DuckLake CDC extension boundary."""

from ducklake_cdc_client import CDCClient

from config import get_str
from repository.catalogue.client import Catalogue


def validate_cdc_extension(catalogue: Catalogue) -> None:
    """Load and validate the image-installed community CDC extension."""

    catalogue.connection.execute("LOAD ducklake_cdc")
    client = CDCClient(catalogue.lake, install_extension=False)
    actual = client.version()
    expected = get_str("ATLAS_DUCKLAKE_CDC_VERSION")
    if actual != expected:
        raise RuntimeError(
            f"DuckLake CDC version mismatch: expected {expected!r}, got {actual!r}"
        )
