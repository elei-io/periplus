"""Open Atlas's configured DuckLake with the native extension-enabled CLI."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile

from atlas.platform.catalogue.config import catalogue_config_from_env
from atlas.platform.catalogue.connection import DuckLakeConnectionFactory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duckdb-cli", type=Path, required=True)
    parser.add_argument("--sql")
    arguments = parser.parse_args()
    config = catalogue_config_from_env()
    factory = DuckLakeConnectionFactory(config)
    init = factory.cli_init_sql(
        read_only=True,
        override_data_path=True,
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
