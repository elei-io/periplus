"""Verify Atlas NATS state and DuckBasin CDC with one disposable lake write."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import uuid

from dotenv import load_dotenv


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe Atlas KV permissions and confirm that a managed DuckLake "
            "DDL/DML commit reaches Basin JetStream."
        )
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("../.env"),
        help="dotenv file loaded before Atlas configuration (default: ../.env)",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=15,
        help="maximum time to wait for each CDC stream to advance",
    )
    arguments = parser.parse_args()
    if arguments.wait_seconds <= 0:
        parser.error("--wait-seconds must be positive")
    return arguments


async def stream_state(jetstream, subject: str) -> dict[str, int | str]:
    stream = await jetstream.find_stream_name_by_subject(subject)
    info = await jetstream.stream_info(stream)
    return {
        "stream": stream,
        "messages": info.state.messages,
        "last_sequence": info.state.last_seq,
    }


async def wait_for_advance(
    jetstream,
    subject: str,
    *,
    after_sequence: int,
    timeout: float,
) -> dict[str, int | str]:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        state = await stream_state(jetstream, subject)
        if int(state["last_sequence"]) > after_sequence:
            return state
        if asyncio.get_running_loop().time() >= deadline:
            return state
        await asyncio.sleep(0.25)


def create_probe(catalogue, table_name: str) -> None:
    catalogue.remote_execute(
        f'CREATE TABLE main."{table_name}" (id BIGINT, value VARCHAR)'
    )
    with catalogue.transaction():
        catalogue.remote_execute(
            f'INSERT INTO main."{table_name}" '
            "VALUES (1, 'basin-cdc-probe')"
        )


def drop_probe(catalogue, table_name: str) -> None:
    catalogue.remote_execute(f'DROP TABLE IF EXISTS main."{table_name}"')


async def verify(wait_seconds: float) -> dict[str, object]:
    from catalogue_relay.executor import BasinDDLEvent, BasinDMLTick
    from config import get_str
    from repository.catalogue import catalogue_from_env
    from runtime.catalogue_events import basin_ddl_subject, basin_dml_subject
    from runtime.catalogue_workers import ensure_catalogue_worker_storage
    from runtime.nats_client import connect_basin_nats, connect_nats

    atlas = await connect_nats()
    basin = await connect_basin_nats()
    catalogue = None
    table_name = "_atlas_cdc_probe_" + uuid.uuid4().hex[:12]
    key = "verification-" + uuid.uuid4().hex
    try:
        bucket = await ensure_catalogue_worker_storage(atlas.jetstream())
        revision = await bucket.put(key, b"ok")
        entry = await bucket.get(key)
        await bucket.purge(key)

        lake = get_str("DUCKBASIN_LAKE")
        ddl_subject = basin_ddl_subject(lake)
        dml_subject = basin_dml_subject(lake)
        basin_js = basin.jetstream()
        ddl_before = await stream_state(basin_js, ddl_subject)
        dml_before = await stream_state(basin_js, dml_subject)

        catalogue = await asyncio.to_thread(catalogue_from_env)
        await asyncio.to_thread(create_probe, catalogue, table_name)
        ddl_after, dml_after = await asyncio.gather(
            wait_for_advance(
                basin_js,
                ddl_subject,
                after_sequence=int(ddl_before["last_sequence"]),
                timeout=wait_seconds,
            ),
            wait_for_advance(
                basin_js,
                dml_subject,
                after_sequence=int(dml_before["last_sequence"]),
                timeout=wait_seconds,
            ),
        )

        payloads: dict[str, object] = {}
        for label, subject, before, model in (
            ("ddl", ddl_subject, ddl_before, BasinDDLEvent),
            ("dml", dml_subject, dml_before, BasinDMLTick),
        ):
            after = ddl_after if label == "ddl" else dml_after
            advanced = int(after["last_sequence"]) > int(
                before["last_sequence"]
            )
            payloads[label] = {
                "advanced": advanced,
                "before": before,
                "after": after,
            }
            if advanced:
                message = await basin_js.get_msg(
                    str(after["stream"]),
                    seq=int(after["last_sequence"]),
                )
                model.model_validate_json(message.data)

        return {
            "atlas_kv": {
                "write": "ok",
                "revision": revision,
                "value": entry.value.decode(),
            },
            "lake": lake,
            "probe_table": table_name,
            "cdc": payloads,
        }
    finally:
        if catalogue is not None:
            await asyncio.to_thread(drop_probe, catalogue, table_name)
            catalogue.close()
        await asyncio.gather(atlas.close(), basin.close())


def main() -> None:
    arguments = parse_arguments()
    load_dotenv(arguments.env_file)
    result = asyncio.run(verify(arguments.wait_seconds))
    print(json.dumps(result, indent=2, sort_keys=True))
    if not all(
        bool(result["cdc"][kind]["advanced"])  # type: ignore[index]
        for kind in ("ddl", "dml")
    ):
        raise SystemExit("Basin CDC streams did not both advance")


if __name__ == "__main__":
    main()
