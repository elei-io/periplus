"""Explicit corpus seed selection through the process-owned read-only query client."""
from datetime import UTC, datetime
import time

import httpx
from sqlglot import exp

from periplus.crawl.runtime.selection_contract import SelectionCheckpoint
from periplus.query.service import QueryRequest, QueryResult, MAX_RESPONSE_BYTES
from periplus.query.validation import _one_statement


class SeedQueryUnavailable(RuntimeError):
    """Keep intent pending until the query service or its credentials recover."""


class SeedQueryClient:
    def __init__(self, url: str, token: str | None, *, transport=None):
        self.token = token
        self.client = httpx.Client(
            base_url=url, headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=httpx.Timeout(25, connect=5, pool=1), follow_redirects=False, trust_env=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1), transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def select(self, request: QueryRequest) -> SelectionCheckpoint:
        if not isinstance(_one_statement(request.sql), exp.Query):
            raise ValueError("seed SQL must be a SELECT query")
        if not self.token:
            raise SeedQueryUnavailable("query service credentials are unavailable")
        deadline = time.monotonic() + 30
        try:
            with self.client.stream("POST", "/query/exec", json=request.model_dump(mode="json")) as response:
                if response.status_code in (408, 422):
                    raise ValueError("seed SQL is invalid or exceeds query execution limits")
                if response.status_code != 200:
                    raise SeedQueryUnavailable("query service is unavailable")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if time.monotonic() > deadline:
                        raise SeedQueryUnavailable("query response transfer exceeded its deadline")
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise SeedQueryUnavailable("query response exceeds the transport limit")
                    body.extend(chunk)
        except httpx.HTTPError as exc:
            raise SeedQueryUnavailable("query service transport is unavailable") from exc
        # Invalid upstream envelopes indicate a service/deployment fault, not bad
        # collection SQL. Keep the request recoverable with bounded backoff.
        try:
            result = QueryResult.model_validate_json(body)
        except ValueError as exc:
            raise SeedQueryUnavailable("query service returned an invalid result") from exc
        if result.truncated:
            raise ValueError("seed SQL output exceeds its limit; select a smaller bounded seed set")
        if result.columns != ["url"] or any(len(row) != 1 for row in result.rows):
            raise ValueError("seed SQL must return exactly one column named url")
        return SelectionCheckpoint(
            urls=tuple(row[0] for row in result.rows), source_snapshot=str(result.source_snapshot),
            source_query_id=result.query_id, selected_at=datetime.now(UTC),
        )
