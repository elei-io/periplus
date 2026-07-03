import json
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .models import QualityWarning, QualityWarningSignal

_WARNINGS_FILE = "warnings.json"


@dataclass(frozen=True)
class QualityContext:
    url: str
    html: str = ""
    crawl: Mapping[str, Any] | None = None
    extraction_results: Sequence[Mapping[str, Any]] | None = None


class WarningSign(ABC):
    code: str
    name: str
    description: str

    @abstractmethod
    def detect(self, context: QualityContext) -> QualityWarning | None:
        pass

    def warning(self, signals: Iterable[tuple[str, Any]]) -> QualityWarning:
        return QualityWarning(
            code=self.code,
            name=self.name,
            description=self.description,
            signals=[
                QualityWarningSignal(name=name, value=value)
                for name, value in signals
            ],
        )


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self.visible_text: list[str] = []
        self.buttons = 0
        self.forms = 0
        self.inputs = 0
        self.links = 0
        self.load_more_markers = 0
        self.interaction_markers = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name: value or "" for name, value in attrs}
        if tag in {"script", "style", "noscript", "template"}:
            self._hidden_depth += 1

        if tag == "button":
            self.buttons += 1
            self._count_marker(attrs_dict)
        elif tag == "form":
            self.forms += 1
        elif tag in {"input", "select", "textarea"}:
            self.inputs += 1
            self._count_marker(attrs_dict)
        elif tag == "a":
            self.links += 1
            self._count_marker(attrs_dict)

        role = attrs_dict.get("role", "").lower()
        if role in {"button", "tab", "option", "menuitem"}:
            self.interaction_markers += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return

        if self._hidden_depth:
            return

        lower_text = text.lower()
        if any(marker in lower_text for marker in ("load more", "show more", "もっと見る")):
            self.load_more_markers += 1
        if re.fullmatch(r"20\d{2}", text) or lower_text in {"next", "previous", "filter", "sort"}:
            self.interaction_markers += 1

        self.visible_text.append(text)

    def _count_marker(self, attrs: Mapping[str, str]) -> None:
        joined = " ".join(attrs.values()).lower()
        if any(marker in joined for marker in ("load-more", "load_more", "show-more", "next-page")):
            self.load_more_markers += 1
        if any(marker in joined for marker in ("tab", "filter", "sort", "dropdown", "combobox")):
            self.interaction_markers += 1


def _parse_html(html: str) -> _VisibleTextParser:
    parser = _VisibleTextParser()
    parser.feed(html)
    return parser


class AppShellWarning(WarningSign):
    code = "app_shell"
    name = "Likely JavaScript App Shell"
    description = (
        "The page looks like a JavaScript application shell. Static extraction may match "
        "layout containers before useful text has hydrated."
    )

    def detect(self, context: QualityContext) -> QualityWarning | None:
        html = context.html
        if not html:
            return None

        parser = _parse_html(html)
        visible_text = " ".join(parser.visible_text)
        script_count = html.lower().count("<script")
        marker_count = sum(
            marker in html
            for marker in (
                "__NEXT_DATA__",
                "next/static",
                "id=\"__next\"",
                "id=\"root\"",
                "data-reactroot",
                "vite",
            )
        )
        visible_ratio = len(visible_text) / max(len(html), 1)
        if marker_count < 1 and script_count < 12:
            return None

        if len(visible_text) > 2500 and visible_ratio > 0.04:
            return None

        return self.warning(
            [
                ("script_count", script_count),
                ("app_markers", marker_count),
                ("visible_text_chars", len(visible_text)),
                ("visible_text_ratio", round(visible_ratio, 4)),
            ]
        )


class LazyLoadWarning(WarningSign):
    code = "lazy_load"
    name = "Possible Lazy Loading"
    description = (
        "The page contains lazy-loading or load-more signals. Results may only include "
        "the initially loaded batch unless the page is scrolled or controls are clicked."
    )

    def detect(self, context: QualityContext) -> QualityWarning | None:
        html = context.html.lower()
        if not html:
            return None

        parser = _parse_html(context.html)
        markers = {
            "load_more_text": parser.load_more_markers,
            "lazy_attributes": len(re.findall(r"\bloading=[\"']lazy[\"']", html)),
            "infinite_scroll_text": html.count("infinite") + html.count("scroll"),
            "cursor_or_page_tokens": len(re.findall(r"\b(cursor|offset|page|next_page)\b", html)),
        }
        score = sum(1 for value in markers.values() if value > 0)
        if score < 2 and parser.load_more_markers < 1:
            return None

        return self.warning(markers.items())


class InteractionRequiredWarning(WarningSign):
    code = "interaction_required"
    name = "Possible Interaction Required"
    description = (
        "The page exposes controls such as tabs, filters, forms, or year buttons. "
        "The requested data may require actions before extraction."
    )

    def detect(self, context: QualityContext) -> QualityWarning | None:
        if not context.html:
            return None

        parser = _parse_html(context.html)
        signals = {
            "buttons": parser.buttons,
            "forms": parser.forms,
            "inputs": parser.inputs,
            "interaction_markers": parser.interaction_markers,
        }
        if parser.interaction_markers < 3 and parser.forms < 1 and parser.buttons < 3:
            return None

        return self.warning(signals.items())


class EmptyExtractionWarning(WarningSign):
    code = "empty_extraction"
    name = "Empty Extraction"
    description = (
        "Extraction completed but returned no records. The page may need different "
        "loading settings, interaction steps, or a different schema."
    )

    def detect(self, context: QualityContext) -> QualityWarning | None:
        if context.extraction_results is None or len(context.extraction_results) > 0:
            return None

        return self.warning([("record_count", 0)])


class EmptyFieldRatioWarning(WarningSign):
    code = "empty_field_ratio"
    name = "High Empty Field Ratio"
    description = (
        "Extraction returned records, but many scalar fields are empty. Selectors may "
        "be matching containers before useful content is available."
    )

    def detect(self, context: QualityContext) -> QualityWarning | None:
        results = context.extraction_results
        if not results:
            return None

        total = 0
        empty = 0
        for item in results:
            for value in _scalar_values(item):
                total += 1
                if value is None or (isinstance(value, str) and not value.strip()):
                    empty += 1

        if total < 3:
            return None

        ratio = empty / total
        if ratio < 0.6:
            return None

        return self.warning(
            [
                ("record_count", len(results)),
                ("scalar_field_count", total),
                ("empty_scalar_field_count", empty),
                ("empty_scalar_field_ratio", round(ratio, 4)),
            ]
        )


class RepeatedExtractionWarning(WarningSign):
    code = "repeated_extraction"
    name = "Repeated Extraction Records"
    description = (
        "Many extracted records are identical. The schema may be matching repeated "
        "containers without selecting distinct content."
    )

    def detect(self, context: QualityContext) -> QualityWarning | None:
        results = context.extraction_results
        if not results or len(results) < 5:
            return None

        fingerprints = [
            json.dumps(item, ensure_ascii=False, sort_keys=True)
            for item in results
        ]
        unique_count = len(set(fingerprints))
        repeated_ratio = 1 - (unique_count / len(fingerprints))
        if repeated_ratio < 0.5:
            return None

        return self.warning(
            [
                ("record_count", len(results)),
                ("unique_record_count", unique_count),
                ("repeated_record_ratio", round(repeated_ratio, 4)),
            ]
        )


def _scalar_values(value: Any) -> Iterable[Any]:
    if isinstance(value, Mapping):
        for child in value.values():
            yield from _scalar_values(child)
    elif isinstance(value, Sequence) and not isinstance(value, str):
        for child in value:
            yield from _scalar_values(child)
    else:
        yield value


WARNING_SIGNS: tuple[WarningSign, ...] = (
    AppShellWarning(),
    LazyLoadWarning(),
    InteractionRequiredWarning(),
    EmptyExtractionWarning(),
    EmptyFieldRatioWarning(),
    RepeatedExtractionWarning(),
)


def run_quality_checks(
    *,
    url: str,
    html: str = "",
    crawl: Mapping[str, Any] | None = None,
    extraction_results: Sequence[Mapping[str, Any]] | None = None,
) -> list[QualityWarning]:
    context = QualityContext(
        url=url,
        html=html,
        crawl=crawl,
        extraction_results=extraction_results,
    )
    warnings: list[QualityWarning] = []
    seen_codes: set[str] = set()
    for warning_sign in WARNING_SIGNS:
        warning = warning_sign.detect(context)
        if warning is None or warning.code in seen_codes:
            continue

        warnings.append(warning)
        seen_codes.add(warning.code)

    return warnings


def warnings_path(cache_dir: str | Path) -> Path:
    return Path(cache_dir) / _WARNINGS_FILE


def write_quality_warnings(cache_dir: str | Path, warnings: Sequence[QualityWarning]) -> Path:
    path = warnings_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([warning.model_dump() for warning in warnings], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path
