# Periplus Python SDK

Read-only access to the public ClickHouse corpus through the Periplus HTTP API.
Install the SDK from the same checkout as your deployment:

```sh
python -m pip install ./packages/periplus-python-sdk
```

```python
from periplus_sdk import Client

with Client("http://localhost:8080") as client:
    result = client.execute(
        "SELECT capture_id, page_url FROM public_v1.capture LIMIT ?", [10]
    )
    print(result.columns, result.rows)
```

Use `PERIPLUS_PUBLIC_URL` to omit the URL argument. No database credentials or
service token are needed for the public gateway. `AsyncClient` provides async
methods. `prepare` validates/explains SQL; `execute` returns typed columns, rows,
truncation and query metadata; `helpers` describes the installed public views.

The public schema is `public_v1`. HTML joins use `document_id` plus node index;
`content_id` identifies raw bytes and can have different interpretations.
There is one query endpoint, with no experimental fallback. Client errors preserve
server categories and do not automatically retry executed queries.

For notebook/SQLAlchemy integration:

```python
from periplus_sdk import sql_api
from sqlalchemy import text

engine = sql_api.create_engine(base_url="http://localhost:8080")
with engine.connect() as connection:
    print(connection.execute(text("SELECT page_url FROM public_v1.capture LIMIT 5")).all())
engine.dispose()
```

The DB-API connection advertises the ClickHouse dialect and converts native
nullable integer, decimal, date and datetime types. Nested types retain JSON wire
values. It is read-only: there are no client transactions or writable sessions.
Streaming cursors expose incomplete/truncated results explicitly; configure
`allow_partial` only when partial results suit the application.

See [the public schema](../../docs/SCHEMA.md) and [query boundary](../../docs/QUERY.md).
