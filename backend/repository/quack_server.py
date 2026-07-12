"""Dedicated DuckDB/Quack host for Atlas's DuckLake."""

from __future__ import annotations

import logging
import signal
import threading
from dataclasses import replace
from pathlib import Path

from config import get_int, get_path, get_str
from repository.catalogue.client import Catalogue
from repository.catalogue.config import catalogue_config_from_env
from repository.ingestion.health import HealthMonitor, start_health_server


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    extension_path = get_path("ATLAS_QUACK_CDC_EXTENSION_PATH").resolve()
    if not extension_path.is_file():
        raise RuntimeError(f"DuckLake CDC extension is missing: {extension_path}")

    config = catalogue_config_from_env()
    config = replace(
        config,
        duckdb=replace(
            config.duckdb,
            config={**config.duckdb.config, "allow_unsigned_extensions": True},
        ),
    )
    monitor = HealthMonitor(heartbeat_timeout_seconds=5)
    health_server, _ = start_health_server(
        address=get_str("ATLAS_QUACK_HEALTH_HOST"),
        port=get_int("ATLAS_QUACK_HEALTH_PORT"),
        monitor=monitor,
    )

    try:
        with Catalogue(config) as catalogue:
            catalogue.validate_schema()
            connection = catalogue.connection
            _load_cdc(connection, extension_path)
            connection.execute("LOAD quack")
            namespace = ".".join(
                f'"{part.replace(chr(34), chr(34) * 2)}"'
                for part in (config.alias, config.schema)
            )
            connection.execute(f"USE {namespace}")
            host = get_str("ATLAS_QUACK_HOST")
            port = get_int("ATLAS_QUACK_PORT")
            token = get_str("ATLAS_QUACK_TOKEN")
            connection.execute(
                "CALL quack_serve(?, token := ?, allow_other_hostname := true)",
                [f"quack:{host}:{port}", token],
            )
            monitor.dependencies_ready()
            logging.info("Atlas Quack server is listening on %s:%d", host, port)
            while not stop.wait(1):
                connection.execute("SELECT 1").fetchone()
                monitor.heartbeat()
            connection.execute("CALL quack_stop(?)", [f"quack:{host}:{port}"])
    except Exception as exc:
        monitor.dependencies_unavailable(str(exc))
        raise
    finally:
        health_server.shutdown()
        health_server.server_close()
    return 0


def _load_cdc(connection, extension_path: Path) -> None:
    escaped = str(extension_path).replace("'", "''")
    connection.execute(f"LOAD '{escaped}'")
    actual = str(connection.execute("SELECT cdc_version()").fetchone()[0])
    expected = get_str("ATLAS_DUCKLAKE_CDC_VERSION")
    if actual != expected:
        raise RuntimeError(
            f"DuckLake CDC version mismatch: expected {expected!r}, got {actual!r}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
