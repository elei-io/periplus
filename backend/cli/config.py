from __future__ import annotations

import json
from pathlib import Path

import typer
from config import get_optional

_CONFIG_NAME = "atlas.json"


def init_config(url: str) -> None:
    path = Path.cwd() / _CONFIG_NAME
    if path.exists():
        raise typer.BadParameter(f"{path} already exists.")
    path.write_text(json.dumps({"api_url": url.rstrip("/")}, indent=2) + "\n")
    typer.echo(f"Created {path}")


def api_url() -> str:
    override = get_optional("ATLAS_API_URL")
    if override:
        return override.rstrip("/")
    for directory in (Path.cwd(), *Path.cwd().parents):
        path = directory / _CONFIG_NAME
        if path.is_file():
            value = json.loads(path.read_text()).get("api_url")
            if isinstance(value, str) and value:
                return value.rstrip("/")
            raise RuntimeError(f"{path} does not contain a valid api_url.")
    raise RuntimeError("Atlas API URL is not configured. Run `atlas init --url <url>`." )
