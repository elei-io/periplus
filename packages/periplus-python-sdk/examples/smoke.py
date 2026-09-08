"""Run against a public app: PERIPLUS_PUBLIC_URL=http://localhost:8080 python smoke.py."""
from periplus_sdk import Client

with Client() as client:
    prepared = client.prepare("SELECT capture_id FROM public_v1.capture LIMIT ?", [1])
    print(prepared.diagnostics)
    result = client.execute(prepared.sql, prepared.parameters)
    print(result.columns, result.rows, result.source_snapshot, result.truncated)
    print(client.helpers().catalogue_version)
