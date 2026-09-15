"""Preserve the exact projection sources and dependency lock with recovery inputs."""

from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from io import BytesIO
import json
import tarfile

from periplus.ingestion.captures import canonical
from periplus.ingestion.objects.store import ObjectStore


def recipe_files() -> dict[str, bytes]:
    root = Path(__file__).resolve().parents[1]
    paths = [
        *root.joinpath("materialization/dom").glob("*.py"),
        root / "materialization/html_content.py",
        root / "materialization/storage.py",
        root / "materialization/schema.sql",
        root / "platform/clickhouse/public.sql",
        root / "ingestion/captures.py",
        root / "ingestion/archive.py",
        root / "ingestion/archive_index.py",
        root / "urls.py",
    ]
    result = {str(path.relative_to(root)): path.read_bytes() for path in paths}
    result["dependencies.json"] = canonical(
        {
            name: version(name)
            for name in ("selectolax", "pydantic", "zstandard", "sqlglot")
        }
    )
    return result


def recipe_digest() -> str:
    return sha256(
        canonical(
            {
                name: sha256(data).hexdigest()
                for name, data in sorted(recipe_files().items())
            }
        )
    ).hexdigest()


def preserve_software(store: ObjectStore) -> str:
    root = Path(__file__).resolve().parents[1]
    package = root.parents[1]
    if not (package / "uv.lock").exists():
        package = Path(__import__("sys").prefix) / "share/periplus"
    inputs = {
        str(path.relative_to(root.parent)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.suffix in (".py", ".sql", ".ini", ".mako")
    }
    for name in ("pyproject.toml", "uv.lock"):
        inputs[name] = (package / name).read_bytes()
    inputs["runtime.json"] = canonical(
        {
            "recipe": recipe_digest(),
            "python": __import__("sys").version,
            "dependencies": {
                name: version(name)
                for name in ("selectolax", "pydantic", "zstandard", "sqlglot")
            },
        }
    )
    target = BytesIO()
    with tarfile.open(fileobj=target, mode="w") as tar:
        for name, data in sorted(inputs.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(data), 0
            tar.addfile(info, BytesIO(data))
    data = target.getvalue()
    key = f"raw/corpus/v1/software/{sha256(data).hexdigest()}.tar"
    store.put_if_absent(key, BytesIO(data))
    verify_software(store, key)
    return key


def verify_software(store: ObjectStore, key: str) -> None:
    import re

    if not re.fullmatch(r"raw/corpus/v1/software/[0-9a-f]{64}\.tar", key):
        raise ValueError("Invalid software artifact identity")
    with store.open(key) as source:
        data = source.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024 or sha256(data).hexdigest() != Path(key).stem:
        raise ValueError("Preserved software is missing or corrupt")
