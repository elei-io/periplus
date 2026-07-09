from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


def default_search_match(base_url: str, search_param_name: str) -> str:
    parsed = urlparse(base_url)
    path = parsed.path or "/"
    base = urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    return f"{base}?{search_param_name}*"


def build_search_url(
    *,
    base_url: str,
    search_param_name: str,
    query: str,
    extra_params: dict[str, Any] | None = None,
) -> str:
    parsed = urlparse(base_url)
    params = {search_param_name: [query]}
    for key, value in parse_qs(parsed.query, keep_blank_values=True).items():
        if key == search_param_name:
            continue
        params[key] = value
    for key, value in (extra_params or {}).items():
        if key == search_param_name or value is None:
            continue
        params[key] = [str(value)]

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/",
            parsed.params,
            urlencode(params, doseq=True),
            parsed.fragment,
        )
    )
