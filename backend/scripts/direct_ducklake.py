"""Open Atlas's configured DuckLake with the native extension-enabled CLI."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile

from atlas.platform.catalogue.config import catalogue_config_from_env


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duckdb-cli", type=Path, required=True)
    parser.add_argument("--sql")
    arguments = parser.parse_args()
    config = catalogue_config_from_env()
    extension_path = config.resolved_extension_path()
    init = "\n".join(
        (
            "INSTALL ducklake; LOAD ducklake;",
            (
                "INSTALL postgres; LOAD postgres;"
                if config.metadata_path.startswith("postgres:")
                else ""
            ),
            f"LOAD {_literal(str(extension_path))};",
            (
                f"ATTACH {_literal('ducklake:' + config.metadata_path)} "
                f"AS {_identifier(config.alias)} "
                f"(DATA_PATH {_literal(config.data_path)}, "
                f"METADATA_SCHEMA {_literal(config.metadata_schema)}, "
                "OVERRIDE_DATA_PATH true, "
                "READ_ONLY);"
            ),
            f"USE {_identifier(config.alias)};",
        )
    )
    with tempfile.TemporaryDirectory(prefix="atlas-duckdb-") as directory:
        init_path = Path(directory) / "init.sql"
        init_path.write_text(init)
        os.chmod(init_path, 0o600)
        command = [str(arguments.duckdb_cli), "-init", str(init_path)]
        if arguments.sql:
            command.extend(("-c", arguments.sql))
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
