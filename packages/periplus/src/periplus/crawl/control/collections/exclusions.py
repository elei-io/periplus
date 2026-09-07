"""Operator URL exclusions apply to both request and background admission."""
import posixpath
import re
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from periplus.crawl.control.domain_policies.schemas import normalize_host_match


class UrlExclusion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    host: str = Field(min_length=1, max_length=253)
    path_prefix: str = Field(default="/", min_length=1, max_length=2048)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        value = normalize_host_match(value)
        if any(character in value for character in "@: \\%"):
            raise ValueError("exclusion host cannot contain ports, credentials, or escapes")
        if value == "*":
            return value
        wildcard = value.startswith("*.")
        host = value[2:] if wildcard else value
        host = host.rstrip(".").encode("idna").decode("ascii")
        if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                  for label in host.split(".")):
            raise ValueError("exclusion host requires a valid hostname")
        return ("*." if wildcard else "") + host

    @field_validator("path_prefix")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value.startswith("/") or "?" in value or "#" in value:
            raise ValueError("exclusion path must be an absolute path without query or fragment")
        return normalized_path(value)

    def matches(self, url: str) -> bool:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        pattern = self.host.rstrip(".")
        matches_host = pattern == "*" or host == pattern or (
            pattern.startswith("*.") and (host == pattern[2:] or host.endswith(pattern[1:])))
        path = normalized_path(parsed.path)
        return matches_host and (self.path_prefix == "/" or path == self.path_prefix
                                 or path.startswith(self.path_prefix + "/"))


def normalized_path(value: str) -> str:
    # Compare decoded path segments, not a string prefix such as /admin matching
    # /administrator. URL identity itself remains unchanged.
    return "/" + posixpath.normpath(unquote(value).replace("\\", "/")).lstrip("/")


def is_excluded(url: str, rules) -> bool:
    return any(UrlExclusion.model_validate(rule).matches(url) for rule in rules)


class UrlExcluded(ValueError):
    pass
