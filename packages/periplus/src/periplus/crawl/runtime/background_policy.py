"""Conservative initial background selection; explicit collections retain their own scope."""
from collections import Counter
from ipaddress import ip_address
from urllib.parse import parse_qsl, unquote, urlsplit

BACKGROUND_RULE = "background-unseen-v1"


def background_rejection(url: str) -> str | None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return "invalid_url"
    if parsed.username is not None or parsed.password is not None:
        return "credentials"
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return "non_public_host"
    try:
        address = ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            return "non_public_host"
    if len(url.encode()) > 2048:
        return "url_length"
    segments = [value.lower() for value in unquote(parsed.path).split("/") if value]
    if len(segments) > 12 or any(count >= 3 for count in Counter(segments).values()):
        return "path_expansion"
    if any(value in {"calendar", "calendars", "session", "sessions"} for value in segments):
        return "calendar_or_session"
    query = parse_qsl(parsed.query, keep_blank_values=True)
    keys = [key.lower() for key, _ in query]
    if len(query) > 4 or len(set(keys)) != len(keys):
        return "query_expansion"
    if any(key in {"sid", "session", "sessionid", "phpsessid", "token", "auth", "replytocom",
                   "sort", "filter", "facet"} or key.startswith(("filter[", "facet[")) for key in keys):
        return "session_or_facet"
    for key, value in query:
        if key.lower() in {"page", "offset"}:
            if not value.isascii() or not value.isdecimal():
                return "pagination"
            if int(value) > (20 if key.lower() == "page" else 200):
                return "pagination"
    return None
