"""Canonical ranked acquisition templates used by policies and policy trials."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from .schemas import CrawlPolicyConfig, CrawlPolicySnapshot

TEMPLATE_REGISTRY_VERSION = 1
_TEMPLATE_NAMESPACE = UUID("7d3af5dc-bbbc-4b83-843e-ab02d8bb731b")


@dataclass(frozen=True)
class CrawlPolicyTemplate:
    name: str
    label: str
    profile: str
    concurrency: int
    config: dict[str, Any]
    provider: bool = False

    def policy_config(self) -> dict[str, Any]:
        return CrawlPolicyConfig(
            profile=self.profile,
            concurrency=self.concurrency,
            config={"template": self.name, **self.config},
        ).model_dump(mode="json")


CRAWL_POLICY_TEMPLATES: tuple[CrawlPolicyTemplate, ...] = (
    CrawlPolicyTemplate("http_fast", "HTTP fast", "http", 10, {}),
    CrawlPolicyTemplate(
        "static_fast", "Static fast", "browser", 10,
        {"mode": "static", "wait": "none", "run_config_overrides": {}},
    ),
    CrawlPolicyTemplate(
        "static_stable", "Static stable", "browser", 10,
        {"mode": "static", "wait": "stable", "run_config_overrides": {}},
    ),
    CrawlPolicyTemplate(
        "dynamic_scan", "Dynamic scan", "browser", 10,
        {"mode": "dynamic", "wait": "none", "run_config_overrides": {}},
    ),
    CrawlPolicyTemplate(
        "dynamic_stable", "Dynamic stable", "browser", 10,
        {"mode": "dynamic", "wait": "stable", "run_config_overrides": {}},
    ),
    CrawlPolicyTemplate(
        "app_stable", "App stable", "browser", 5,
        {"mode": "app", "wait": "stable", "run_config_overrides": {}},
    ),
    CrawlPolicyTemplate(
        "app_deep", "App deep", "browser", 5,
        {
            "mode": "app",
            "wait": "stable",
            "run_config_overrides": {
                "delay_before_return_html": 6.0,
                "max_scroll_steps": 8,
                "scroll_delay": 1.0,
            },
        },
    ),
    CrawlPolicyTemplate(
        "provider", "External provider", "firecrawl", 4, {}, provider=True
    ),
)

_BY_NAME = {template.name: template for template in CRAWL_POLICY_TEMPLATES}


def get_crawl_policy_template(name: str) -> CrawlPolicyTemplate | None:
    return _BY_NAME.get(name)


def template_for_config(config_json: dict | None) -> CrawlPolicyTemplate:
    envelope = CrawlPolicyConfig.model_validate(config_json or {})
    parsed = envelope.parsed_config()
    configured = str(getattr(parsed, "template", "") or "")
    if configured in _BY_NAME:
        return _BY_NAME[configured]
    if envelope.profile == "http":
        return _BY_NAME["http_fast"]
    if envelope.profile == "firecrawl":
        return _BY_NAME["provider"]
    mode = str(getattr(parsed, "mode", "static"))
    wait = str(getattr(parsed, "wait", "none"))
    overrides = dict(getattr(parsed, "run_config_overrides", {}) or {})
    if mode == "app" and int(overrides.get("max_scroll_steps", 0) or 0) >= 8:
        return _BY_NAME["app_deep"]
    if mode == "app":
        return _BY_NAME["app_stable"]
    if mode == "dynamic" and wait != "none":
        return _BY_NAME["dynamic_stable"]
    if mode == "dynamic":
        return _BY_NAME["dynamic_scan"]
    if wait != "none":
        return _BY_NAME["static_stable"]
    return _BY_NAME["static_fast"]


def template_for_policy(snapshot_json: dict | None) -> CrawlPolicyTemplate:
    if snapshot_json is None:
        return _BY_NAME["http_fast"]
    snapshot = CrawlPolicySnapshot.model_validate(snapshot_json)
    return template_for_config(snapshot.config)


def next_trial_policy_snapshot(
    snapshot_json: dict | None,
    *,
    url: str,
    provider_enabled: bool,
) -> tuple[dict, str] | None:
    current = template_for_policy(snapshot_json)
    index = CRAWL_POLICY_TEMPLATES.index(current)
    candidates = CRAWL_POLICY_TEMPLATES[index + 1 :]
    candidate = next(
        (value for value in candidates if provider_enabled or not value.provider),
        None,
    )
    if candidate is None:
        return None
    incumbent = (
        CrawlPolicySnapshot.model_validate(snapshot_json)
        if snapshot_json is not None
        else None
    )
    parsed = urlsplit(url)
    policy_config = candidate.policy_config()
    policy_config["config"]["cache"] = {"mode": "refresh"}
    snapshot = CrawlPolicySnapshot(
        id=uuid5(_TEMPLATE_NAMESPACE, candidate.name),
        revision=TEMPLATE_REGISTRY_VERSION,
        origin="system_trial",
        metric_slug=f"trial-{candidate.name}",
        domain_group=(incumbent.domain_group if incumbent else "unclassified"),
        match=f"{parsed.scheme}://{parsed.hostname or ''}/*",
        config=policy_config,
        matcher=(
            incumbent.matcher
            if incumbent is not None
            else {
                "scheme": parsed.scheme,
                "host": parsed.hostname or "",
                "path_pattern": "/*",
                "match_type": "glob",
                "priority": 0,
            }
        ),
    )
    return snapshot.model_dump(mode="json"), candidate.name
