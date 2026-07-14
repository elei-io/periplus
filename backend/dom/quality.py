"""Compact document-level quality measurements derived from the canonical DOM."""

from __future__ import annotations

import re
from dataclasses import dataclass

from dom.encoder import ElementRow

QUALITY_SCHEMA_VERSION = 1
_HIDDEN_TAGS = {"script", "style", "noscript", "template"}


@dataclass(frozen=True, slots=True)
class DocumentQuality:
    quality_schema_version: int
    html_character_count: int
    visible_text_chars: int
    script_count: int
    app_marker_count: int
    lazy_marker_count: int
    interaction_marker_count: int
    button_count: int
    form_count: int
    input_count: int
    anchor_count: int
    flag_codes: tuple[str, ...]


class DocumentQualityAccumulator:
    """Collect stable primitive counts while element rows are already being streamed."""

    def __init__(self, *, html_character_count: int) -> None:
        self.html_character_count = html_character_count
        self.visible_text_chars = 0
        self.script_count = 0
        self.app_marker_count = 0
        self.lazy_marker_count = 0
        self.interaction_marker_count = 0
        self.button_count = 0
        self.form_count = 0
        self.input_count = 0
        self.anchor_count = 0
        self._hidden_until = -1

    def observe(self, row: ElementRow) -> None:
        tag = row.tag.lower()
        attributes = {key.lower(): value for key, value in row.attributes.items()}
        inside_hidden = row.element_index <= self._hidden_until
        if tag in _HIDDEN_TAGS:
            self._hidden_until = max(self._hidden_until, row.subtree_end_index)

        if tag == "script":
            self.script_count += 1
        elif tag == "button":
            self.button_count += 1
        elif tag == "form":
            self.form_count += 1
        elif tag in {"input", "select", "textarea"}:
            self.input_count += 1
        elif tag == "a":
            self.anchor_count += 1

        identifier = attributes.get("id", "").lower()
        joined_attributes = " ".join(attributes.values()).lower()
        if identifier in {"__next", "root"}:
            self.app_marker_count += 1
        if "data-reactroot" in attributes:
            self.app_marker_count += 1
        if "next/static" in joined_attributes or "vite" in joined_attributes:
            self.app_marker_count += 1

        if attributes.get("loading", "").lower() == "lazy":
            self.lazy_marker_count += 1
        if any(
            marker in joined_attributes
            for marker in ("load-more", "load_more", "show-more", "next-page")
        ):
            self.lazy_marker_count += 1
        if any(
            marker in joined_attributes
            for marker in ("infinite", "scroll", "cursor", "next_page")
        ):
            self.lazy_marker_count += 1

        role = attributes.get("role", "").lower()
        if role in {"button", "tab", "option", "menuitem"}:
            self.interaction_marker_count += 1
        if any(
            marker in joined_attributes
            for marker in ("tab", "filter", "sort", "dropdown", "combobox")
        ):
            self.interaction_marker_count += 1

        if not inside_hidden and tag not in _HIDDEN_TAGS:
            self._observe_visible_text(row.text_direct)
        if not inside_hidden:
            self._observe_visible_text(row.text_tail)

    def finish(self) -> DocumentQuality:
        visible_ratio = self.visible_text_chars / max(self.html_character_count, 1)
        flags: list[str] = []
        if (
            (self.app_marker_count > 0 or self.script_count >= 12)
            and (self.visible_text_chars <= 2500 or visible_ratio <= 0.04)
        ):
            flags.append("app_shell")
        if self.lazy_marker_count > 0:
            flags.append("lazy_load")
        if (
            self.interaction_marker_count >= 3
            or self.form_count > 0
            or self.button_count >= 3
        ):
            flags.append("interaction_required")
        return DocumentQuality(
            quality_schema_version=QUALITY_SCHEMA_VERSION,
            html_character_count=self.html_character_count,
            visible_text_chars=self.visible_text_chars,
            script_count=self.script_count,
            app_marker_count=self.app_marker_count,
            lazy_marker_count=self.lazy_marker_count,
            interaction_marker_count=self.interaction_marker_count,
            button_count=self.button_count,
            form_count=self.form_count,
            input_count=self.input_count,
            anchor_count=self.anchor_count,
            flag_codes=tuple(flags),
        )

    def _observe_visible_text(self, value: str) -> None:
        text = value.strip()
        if not text:
            return
        self.visible_text_chars += len(text)
        lower = text.lower()
        if "load more" in lower or "show more" in lower or "もっと見る" in lower:
            self.lazy_marker_count += 1
        if re.fullmatch(r"20\d{2}", text) or lower in {
            "next",
            "previous",
            "filter",
            "sort",
        }:
            self.interaction_marker_count += 1
