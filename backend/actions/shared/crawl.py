from hashlib import sha1
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from crawl4ai import BrowserConfig, CrawlerRunConfig
    from crawl4ai.models import CrawlResult
else:
    BrowserConfig = CrawlerRunConfig = CrawlResult = Any

CrawlMode = Literal["static", "dynamic", "app"]
CrawlWait = Literal["none", "stable", "network", "fixed"]

_FIXED_WAIT_SECONDS = 10.0


def stable_wait_config() -> dict[str, str]:
    return {
        "js_code_before_wait": """
window.__atlasStableStartedAt = Date.now();
window.__atlasLastLinkCount = document.links.length;
window.__atlasLastMutationAt = Date.now();
window.__atlasStableObserver?.disconnect?.();
window.__atlasStableObserver = new MutationObserver(() => {
  window.__atlasLastMutationAt = Date.now();
});
window.__atlasStableObserver.observe(document.documentElement, {
  childList: true,
  subtree: true,
  attributes: true,
});
""",
        "wait_for": """js:() => {
  const count = document.links.length;
  const now = Date.now();
  if (window.__atlasLastLinkCount !== count) {
    window.__atlasLastLinkCount = count;
    window.__atlasLastMutationAt = now;
    return false;
  }
  const pageHasHadTimeToHydrate = now - (window.__atlasStableStartedAt || now) >= 3000;
  const hasUsableLinks = count > 0;
  const hasBeenQuiet = now - (window.__atlasLastMutationAt || now) >= 1500;
  return document.readyState === "complete" && hasBeenQuiet && (hasUsableLinks || pageHasHadTimeToHydrate);
}""",
    }


def wait_config(wait: CrawlWait) -> dict[str, str]:
    if wait == "stable":
        return stable_wait_config()

    return {}


def wait_until_for_wait(wait: CrawlWait) -> str:
    if wait == "network":
        return "networkidle"

    return "domcontentloaded"


def run_config_for_mode(
    mode: CrawlMode,
    wait: CrawlWait,
    **overrides,
) -> CrawlerRunConfig:
    from crawl4ai import CacheMode, CrawlerRunConfig

    config = {
        "cache_mode": CacheMode.BYPASS,
        "magic": True,
        "verbose": False,
        "wait_until": wait_until_for_wait(wait),
        **wait_config(wait),
    }

    if mode == "dynamic":
        config.update(
            {
                "scan_full_page": True,
                "scroll_delay": 0.5,
                "delay_before_return_html": _FIXED_WAIT_SECONDS if wait == "fixed" else 2.0,
            }
        )
    elif mode == "app":
        config.update(
            {
                "scan_full_page": True,
                "scroll_delay": 0.75,
                "delay_before_return_html": _FIXED_WAIT_SECONDS if wait == "fixed" else 4.0,
            }
        )
    elif wait == "fixed":
        config["delay_before_return_html"] = _FIXED_WAIT_SECONDS

    config.update(overrides)
    return CrawlerRunConfig(**config)


def browser_config_for_mode(mode: CrawlMode) -> BrowserConfig:
    from crawl4ai import BrowserConfig

    return BrowserConfig(
        headless=True,
        enable_stealth=True,
        text_mode=mode == "static",
        light_mode=mode == "static",
        verbose=False,
    )


def session_id(url: str) -> str:
    return f"atlas-crawl-{sha1(url.encode()).hexdigest()}"


def app_pre_scan_wait_config(
    url: str,
    wait: CrawlWait,
    session_id_override: str | None = None,
) -> CrawlerRunConfig:
    return run_config_for_mode(
        mode="static",
        wait=wait,
        delay_before_return_html=_FIXED_WAIT_SECONDS if wait == "fixed" else 0.1,
        session_id=session_id_override or session_id(url),
    )


def app_scan_config(url: str, **overrides) -> CrawlerRunConfig:
    from crawl4ai import CacheMode, CrawlerRunConfig

    config = {
        "cache_mode": CacheMode.BYPASS,
        "magic": True,
        "verbose": False,
        "js_only": True,
        "session_id": session_id(url),
        "scan_full_page": True,
        "scroll_delay": 0.75,
        "delay_before_return_html": 4.0,
    }
    config.update(overrides)
    return CrawlerRunConfig(**config)


async def crawl_single_url(
    crawler,
    url: str,
    run_config: CrawlerRunConfig,
    mode: CrawlMode,
    wait: CrawlWait,
) -> CrawlResult:
    if mode == "app" and wait != "none":
        shared_session_id = getattr(run_config, "session_id", None)
        await crawler.arun(
            url=url,
            config=app_pre_scan_wait_config(url, wait=wait, session_id_override=shared_session_id),
        )
        overrides = {
            key: value
            for key, value in run_config.__dict__.items()
            if key
            in {
                "js_code",
                "js_code_before_wait",
                "js_only",
                "session_id",
                "wait_for",
                "wait_for_timeout",
                "delay_before_return_html",
                "scan_full_page",
                "scroll_delay",
                "max_scroll_steps",
            }
            and value is not None
        }
        return await crawler.arun(url=url, config=app_scan_config(url, **overrides))

    return await crawler.arun(url=url, config=run_config)
