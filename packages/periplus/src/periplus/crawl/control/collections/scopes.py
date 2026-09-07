"""URL-section matching shared by collection seed and link admission."""
from pydantic import HttpUrl, TypeAdapter


def within_allowed_sections(url: str, sections: list[str]) -> bool:
    if not sections:
        return True
    value = str(TypeAdapter(HttpUrl).validate_python(url)).split("?", 1)[0].split("#", 1)[0]
    return any(value == section or value.startswith(section + "/") for section in sections)
