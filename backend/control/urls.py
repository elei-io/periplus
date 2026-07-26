from urllib.parse import urldefrag, urlsplit, urlunsplit


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
    # Query strings are opaque. Reordering, decoding, re-encoding, or removing
    # apparent tracking parameters can invalidate signed navigation URLs.
    return urlunsplit(
        (scheme, netloc, parsed.path or "/", parsed.query, "")
    )
