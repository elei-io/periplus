# Atlas SDK

Typed sync and async Python clients for Atlas.

```python
from atlas_sdk import AtlasClient

with AtlasClient("https://atlas.example.com", token="...") as atlas:
    result = atlas.compiler.compile("SELECT * FROM documents LIMIT 10")

if result.valid and result.executable_sql is not None:
    print(result.executable_sql)
```

The base SDK is remote-only and does not depend on DuckDB, SQLGlot, Atlas
workers, or Atlas storage services.
