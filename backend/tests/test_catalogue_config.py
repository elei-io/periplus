from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from atlas.platform.catalogue.__main__ import main as catalogue_main
from atlas.platform.catalogue.config import (
    CatalogueConfig,
    catalogue_config_from_env,
)
from atlas.platform.catalogue.exceptions import CatalogueConfigError


class CatalogueConfigTests(unittest.TestCase):
    def test_extension_path_is_required(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                CatalogueConfigError,
                "ATLAS_DUCKDB_EXTENSION_PATH",
            ):
                catalogue_config_from_env()

    def test_extension_path_must_identify_a_file(self) -> None:
        config = CatalogueConfig(
            alias="atlas",
            metadata_path="metadata.duckdb",
            data_path="lake/",
            metadata_schema="ducklake",
            extension_path="/missing/atlas.duckdb_extension",
            cdc_extension_path="/missing/ducklake_cdc.duckdb_extension",
        )

        with self.assertRaisesRegex(
            CatalogueConfigError,
            "was not found",
        ):
            config.resolved_extension_path()

    def test_extension_path_resolves_existing_file(self) -> None:
        with TemporaryDirectory() as directory:
            extension = Path(directory) / "atlas.duckdb_extension"
            extension.touch()
            config = CatalogueConfig(
                alias="atlas",
                metadata_path="metadata.duckdb",
                data_path="lake/",
                metadata_schema="ducklake",
                extension_path=str(extension),
                cdc_extension_path=str(extension),
            )

            self.assertEqual(
                config.resolved_extension_path(),
                extension.resolve(),
            )

    def test_cdc_extension_path_is_required_when_loaded(self) -> None:
        config = CatalogueConfig(
            alias="atlas",
            metadata_path="metadata.duckdb",
            data_path="lake/",
            metadata_schema="ducklake",
            extension_path="/missing/atlas.duckdb_extension",
            cdc_extension_path="",
        )

        with self.assertRaisesRegex(
            CatalogueConfigError,
            "ATLAS_DUCKLAKE_CDC_EXTENSION_PATH",
        ):
            config.resolved_cdc_extension_path()

    @patch("atlas.platform.catalogue.__main__.catalogue_from_env")
    def test_check_uses_a_read_only_portable_host_attachment(
        self,
        catalogue_from_env,
    ) -> None:
        catalogue = MagicMock()
        catalogue_from_env.return_value.__enter__.return_value = catalogue

        self.assertEqual(catalogue_main(["check"]), 0)

        catalogue_from_env.assert_called_once_with(
            read_only=True,
            override_data_path=True,
        )
        catalogue.validate_schema.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
