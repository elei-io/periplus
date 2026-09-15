"""Bounded ClickHouse catalogue inspection and verified raw downloads."""

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
import tempfile
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from starlette.responses import StreamingResponse
from sqlalchemy import text

from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.ingestion.objects.document import ExactDocumentRepository
from periplus.materialization.rebuilds.control import BuildControl
from periplus.platform.clickhouse.public import PUBLIC_RELATIONS
from periplus.platform.postgres.session import SessionLocal

router = APIRouter()


@router.get("/sql/metadata")
async def metadata(request: Request):
    binding = await asyncio.to_thread(BuildControl().binding)
    reader = request.app.state.crawl_results
    database = binding["database"]
    rows = (
        await reader._read(
            "SELECT table, name, type FROM system.columns WHERE database={database:String} ORDER BY table, position",
            {"database": database},
        )
    )["data"]
    version = (await reader._read("SELECT version() AS version"))["data"][0]["version"]
    return {
        "catalogue_version": "public_v1",
        "engine": "ClickHouse",
        "engine_version": version,
        "macros": [],
        "relations": [
            {
                "description": None,
                "schema_name": "public_v1",
                "name": relation,
                "kind": "view",
                "columns": [
                    {
                        "name": row["name"],
                        "data_type": row["type"],
                        "description": None,
                        "nullable": "Nullable(" in row["type"],
                    }
                    for row in rows
                    if row["table"] == relation
                ],
            }
            for relation in sorted(PUBLIC_RELATIONS)
        ],
    }


@router.get("/operations/storage")
async def storage(request: Request):
    reader = request.app.state.crawl_results
    tables = (
        await reader._read(
            "SELECT database, table AS name, sum(rows) AS rows, sum(bytes_on_disk) AS bytes, "
            "sum(data_uncompressed_bytes) AS uncompressed_bytes, count() AS parts FROM system.parts WHERE active "
            "AND (database='material' OR startsWith(database,'material_')) GROUP BY database, table ORDER BY database, table"
        )
    )["data"]
    disks = (
        await reader._read("SELECT name, total_space, free_space FROM system.disks")
    )["data"]
    merges = (
        await reader._read(
            "SELECT database, table AS name, elapsed, progress, memory_usage FROM system.merges"
        )
    )["data"]

    def postgres():
        with SessionLocal() as session:
            return list(
                session.execute(
                    text(
                        "SELECT relname AS name, pg_total_relation_size(relid) AS bytes, n_live_tup AS estimated_rows "
                        "FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC"
                    )
                ).mappings()
            )

    control = await asyncio.to_thread(postgres)
    streams = [
        {
            "name": item.config.name,
            "bytes": item.state.bytes,
            "messages": item.state.messages,
        }
        for item in await request.app.state.jetstream.streams_info()
    ]
    return {
        "collected_at": datetime.now(UTC),
        "tables": tables,
        "disks": disks,
        "merges": merges,
        "control_tables": control,
        "streams": streams,
        "sources": [
            {
                "id": "clickhouse",
                "name": "ClickHouse active parts",
                "bytes": sum(row["bytes"] for row in tables),
                "complete": False,
                "basis": "Active evidence and material parts",
                "reason": "Excludes inactive parts and server logs.",
            },
            {
                "id": "control",
                "name": "Postgres tables and indexes",
                "bytes": sum(row["bytes"] for row in control),
                "complete": False,
                "basis": "pg_total_relation_size",
                "reason": "Excludes WAL and database overhead.",
            },
            {
                "id": "nats",
                "name": "JetStream messages",
                "bytes": sum(row["bytes"] for row in streams),
                "complete": False,
                "basis": "Stream storage accounting",
                "reason": "Excludes filesystem overhead.",
            },
            {
                "id": "raw",
                "name": "Raw object storage",
                "bytes": None,
                "complete": False,
                "basis": "Not inventoried",
                "reason": "Raw bytes are retained; destructive retention is disabled.",
            },
        ],
    }


def _verified_file(store, row):
    source = (
        RawHtmlRepository(store).iter_bytes(row["object_key"])
        if row["storage_encoding"] == "zstd"
        else ExactDocumentRepository(store).iter_bytes(row["object_key"])
    )
    output = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    digest, size = sha256(), 0
    try:
        for chunk in source:
            size += len(chunk)
            if size > row["content_bytes"] or size > 64 * 1024 * 1024:
                raise ValueError("Raw content exceeds its verified size budget")
            digest.update(chunk)
            output.write(chunk)
        if size != row["content_bytes"] or digest.hexdigest() != row["digest"]:
            raise ValueError("Raw content digest mismatch")
        output.seek(0)
        return output
    except BaseException:
        output.close()
        raise


@router.get("/documents/{document_id}/content")
async def download(
    document_id: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")], request: Request
):
    slot = request.app.state.download_slot
    if slot.locked():
        raise HTTPException(
            429, "Raw download capacity is busy", headers={"Retry-After": "1"}
        )
    await slot.acquire()
    try:
        database = await request.app.state.crawl_results.material_database()
        rows = (
            await request.app.state.crawl_results._read(
                f"SELECT object_key, storage_encoding, byte_length AS content_bytes, lower(hex(content_id)) AS digest "
                f"FROM {database}.captures WHERE document_id=unhex({{digest:String}}) LIMIT 1",
                {"digest": document_id},
            )
        )["data"]
        if not rows:
            raise HTTPException(404, "Content not found")
        if rows[0]["storage_encoding"] not in ("identity", "zstd"):
            raise HTTPException(503, "Stored content encoding is unsupported")
        # Verify before sending HTTP success. Disk spooling bounds memory; this is
        # temporary transfer state and is closed after completion/disconnection.
        if rows[0]["content_bytes"] > 64 * 1024 * 1024:
            raise HTTPException(413, "Content exceeds the 64 MiB download budget")
        operation = asyncio.create_task(
            asyncio.to_thread(_verified_file, request.app.state.document_store, rows[0])
        )
        try:
            file = await asyncio.shield(operation)
        except BaseException:
            try:
                file = await asyncio.shield(operation)
                file.close()
            finally:
                raise
    except BaseException as exc:
        slot.release()
        if isinstance(exc, Exception) and not isinstance(exc, HTTPException):
            raise HTTPException(503, "Raw content could not be verified") from exc
        raise

    def body():
        try:
            while chunk := file.read(1024 * 1024):
                yield chunk
        finally:
            file.close()

    from starlette.background import BackgroundTask

    async def cleanup():
        file.close()
        slot.release()

    return StreamingResponse(
        body(),
        media_type="application/octet-stream",
        background=BackgroundTask(cleanup),
        headers={
            "Content-Disposition": f'attachment; filename="{document_id}"',
            "ETag": f'"{document_id}"',
            "Content-Length": str(rows[0]["content_bytes"]),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


from periplus.query.models import QueryRequest


@router.post("/admin/sql/exec")
async def admin_sql(payload: QueryRequest, request: Request):
    """Privileged single-statement native SQL; uncertain writes are never retried."""
    import json
    import sqlglot
    from sqlglot import exp
    from periplus.platform.clickhouse import connect_clickhouse, ClickHouseError
    from periplus.query.binding import PublicationBinding, bind_publication
    from periplus.query.service import wire_value
    from periplus.query.errors import query_error

    if request.state.api_role != "admin":
        raise HTTPException(403, "Administrative credential required")
    if payload.parameters:
        raise HTTPException(422, "The native operator console accepts literal SQL only")
    try:
        statements = sqlglot.parse(payload.sql, read="clickhouse")
    except sqlglot.errors.ParseError as exc:
        raise HTTPException(422, "Invalid ClickHouse SQL") from exc
    if len(statements) != 1 or statements[0] is None:
        raise HTTPException(422, "Submit one statement")
    tree = statements[0]
    if any(
        node.args.get("format")
        or node.args.get("settings")
        or isinstance(node, (exp.Into, exp.Transaction, exp.Commit, exp.Rollback))
        for node in tree.walk()
    ):
        raise HTTPException(
            422, "Output formats, settings and transactions are service-owned"
        )
    slot = request.app.state.admin_sql_slot
    if slot.locked():
        raise HTTPException(429, "Operator SQL is busy")
    async with slot:
        binding = PublicationBinding(**await asyncio.to_thread(BuildControl().binding))

        def execute():
            client = connect_clickhouse()
            try:
                sql = tree.sql(dialect="clickhouse")
                if isinstance(tree, (exp.Select, exp.SetOperation)):
                    sql = bind_publication(sql, binding)
                if isinstance(
                    tree, (exp.Select, exp.SetOperation, exp.Describe, exp.Show)
                ):
                    if isinstance(tree, (exp.Select, exp.SetOperation)):
                        sql = f"SELECT * FROM ({sql}) LIMIT 1001"
                    result = json.loads(client.execute(sql + " FORMAT JSONCompact"))
                    return {
                        "columns": [item["name"] for item in result["meta"]],
                        "types": [item["type"] for item in result["meta"]],
                        "rows": wire_value(result["data"][:1000]),
                        "truncated": len(result["data"]) > 1000,
                    }
                client.execute(sql)
                return {"columns": [], "types": [], "rows": [], "truncated": False}
            finally:
                client.close()

        try:
            return await asyncio.to_thread(execute)
        except ClickHouseError as exc:
            if isinstance(tree, (exp.Select, exp.SetOperation, exp.Describe, exp.Show)):
                status, error = query_error(exc)
                raise HTTPException(status, error.detail) from exc
            raise HTTPException(
                422,
                f"ClickHouse rejected the operation (code {exc.code}). A lost write response does not establish rollback.",
            ) from exc
