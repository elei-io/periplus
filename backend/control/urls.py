from urllib.parse import parse_qsl, urlencode, urldefrag, urlsplit, urlunsplit


def normalize_url(value: str) -> str:
    url, _ = urldefrag(value.strip())
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError(f"URL must be absolute HTTP(S): {value}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not supported")
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    netloc = (
        host
        if port is None or (scheme, port) in {("http", 80), ("https", 443)}
        else f"{host}:{port}"
    )
    query = urlencode(
        sorted(parse_qsl(parsed.query, keep_blank_values=True)),
        doseq=True,
    )
    return urlunsplit((scheme, netloc, parsed.path or "/", query, ""))
