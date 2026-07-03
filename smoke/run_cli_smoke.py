from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
RUNS = ROOT / "smoke" / "runs"


@dataclass(frozen=True)
class Knob:
    mode: str
    wait: str

    @property
    def grade(self) -> str:
        if self.mode == "static" and self.wait == "none":
            return "A"
        if self.mode == "dynamic" and self.wait in {"none", "stable"}:
            return "B"
        if self.mode == "app" and self.wait == "stable":
            return "C"
        return "D"


@dataclass(frozen=True)
class Site:
    site_id: str
    url: str
    purpose: str
    prompt: str
    must_contain: tuple[str, ...] = ()
    index_terms: tuple[str, ...] = ()
    knobs: tuple[Knob, ...] = (
        Knob("static", "none"),
        Knob("dynamic", "stable"),
        Knob("app", "stable"),
    )


SITES: tuple[Site, ...] = (
    Site(
        "example",
        "https://example.com",
        "tiny static baseline",
        "Extract the page title, main heading, and visible links.",
        ("Example Domain",),
        ("Learn more",),
    ),
    Site(
        "httpbin_html",
        "https://httpbin.org/html",
        "simple static HTTP response",
        "Extract the page heading and the first paragraph.",
        ("Herman Melville",),
        (),
    ),
    Site(
        "books_to_scrape",
        "https://books.toscrape.com/",
        "static ecommerce catalog",
        "Extract book listings with title, price, rating, availability, and detail URL.",
        ("Books to Scrape", "A Light in the Attic"),
        ("catalogue", "Travel"),
    ),
    Site(
        "quotes_static",
        "https://quotes.toscrape.com/",
        "static quote cards with pagination",
        "Extract quotes with quote text, author, and tags.",
        ("Albert Einstein", "The world as we have created it"),
        ("Login", "change our thinking"),
    ),
    Site(
        "quotes_js",
        "https://quotes.toscrape.com/js/",
        "JavaScript-rendered quote cards",
        "Extract quotes with quote text, author, and tags.",
        ("Albert Einstein", "The world as we have created it"),
        ("Login",),
    ),
    Site(
        "quotes_scroll",
        "https://quotes.toscrape.com/scroll",
        "infinite-scroll quote page",
        "Extract visible quotes with quote text, author, and tags.",
        ("Albert Einstein", "The world as we have created it"),
        (),
    ),
    Site(
        "scrape_countries",
        "https://www.scrapethissite.com/pages/simple/",
        "simple repeated country facts",
        "Extract countries with name, capital, population, and area.",
        ("Countries of the World", "Andorra"),
        (),
    ),
    Site(
        "scrape_ajax",
        "https://www.scrapethissite.com/pages/ajax-javascript/",
        "AJAX-loaded table/list content",
        "Extract Oscar winning films with title, year, awards, and nominations.",
        ("2015", "Best Picture"),
        (),
    ),
    Site(
        "webscraper_static",
        "https://webscraper.io/test-sites/e-commerce/static",
        "training ecommerce site with links",
        "Extract product categories and visible product listings.",
        ("E-commerce training site", "Phones"),
        ("Computers", "Phones"),
    ),
    Site(
        "webscraper_more",
        "https://webscraper.io/test-sites/e-commerce/more",
        "load-more ecommerce training site",
        "Extract visible product listings with name, price, description, and rating.",
        ("E-commerce training site", "Load more"),
        ("Phones",),
    ),
    Site(
        "webscraper_scroll",
        "https://webscraper.io/test-sites/e-commerce/scroll",
        "infinite-scroll ecommerce training site",
        "Extract visible product listings with name, price, description, and rating.",
        ("E-commerce training site", "Phones"),
        ("Phones",),
    ),
    Site(
        "the_internet_tables",
        "https://the-internet.herokuapp.com/tables",
        "automation demo page with HTML tables",
        "Extract table rows with last name, first name, email, due, and website.",
        ("Data Tables", "jsmith@gmail.com"),
        (),
    ),
    Site(
        "practice_expand",
        "https://practice.expandtesting.com/",
        "automation practice portal with many examples",
        "Extract sample application cards with title, description, and URL.",
        ("Automation", "Selenium"),
        ("Web inputs", "Examples"),
    ),
    Site(
        "wikipedia_web_scraping",
        "https://en.wikipedia.org/wiki/Web_scraping",
        "real dense article page",
        "Extract article title, lead summary, section headings, and references.",
        ("Web scraping", "Data scraping"),
        ("References", "History"),
    ),
    Site(
        "mercari_jp",
        "https://jp.mercari.com/",
        "real JavaScript app known to need app/stable",
        "Extract the main navigation links, search affordance, and visible marketplace content.",
        ("メルカリ",),
        ("search", "mercari"),
    ),
)


SEARCH_QUERIES = (
    "Atlas python web scraping",
    "site:docs.python.org asyncio task group",
)


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def command_base() -> list[str]:
    return ["uv", "run", "atlas"]


def run_command(args: list[str], out_dir: Path, slug: str, timeout: int = 180) -> dict[str, Any]:
    command = command_base() + args
    start = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        completed = subprocess.run(
            command,
            cwd=BACKEND,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        timed_out = False
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""

    duration = time.perf_counter() - start
    stdout_path = out_dir / "stdout" / f"{slug}.txt"
    stderr_path = out_dir / "stderr" / f"{slug}.txt"
    stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
    stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
    return {
        "command": " ".join(command),
        "started_at": started_at,
        "duration_seconds": round(duration, 3),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout_path": str(stdout_path.relative_to(out_dir)),
        "stderr_path": str(stderr_path.relative_to(out_dir)),
        "stdout": stdout,
        "stderr": stderr,
    }


def visible_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def output_has_terms(output: str, terms: tuple[str, ...]) -> dict[str, bool]:
    lower = output.lower()
    return {term: term.lower() in lower for term in terms}


def scrape_passed(result: dict[str, Any], site: Site) -> tuple[bool, str, dict[str, Any]]:
    output = result["stdout"] + "\n" + result["stderr"]
    lower = output.lower()
    artifact_counts = [int(value) for value in re.findall(r"Wrote\s+(\d+)\s+artifacts", output)]
    byte_counts = [int(value) for value in re.findall(r"\((?:[^)]*?,\s*)?(\d+)\s+bytes\)", output)]
    if result["exit_code"] != 0:
        return False, "non-zero exit", {"artifact_counts": artifact_counts, "byte_counts": byte_counts}
    if re.search(r"│\s*no\s*│", lower):
        return False, "CLI reported a failed page", {"artifact_counts": artifact_counts, "byte_counts": byte_counts}
    if artifact_counts and max(artifact_counts) == 0:
        return False, "no artifacts written", {"artifact_counts": artifact_counts, "byte_counts": byte_counts}
    if byte_counts and max(byte_counts) == 0:
        return False, "zero bytes written", {"artifact_counts": artifact_counts, "byte_counts": byte_counts}
    return True, "scrape wrote non-empty artifacts", {
        "artifact_counts": artifact_counts,
        "byte_counts": byte_counts,
    }


def table_passed(result: dict[str, Any], site: Site, terms: tuple[str, ...]) -> tuple[bool, str, dict[str, Any]]:
    output = result["stdout"] + "\n" + result["stderr"]
    seen = output_has_terms(output, terms)
    if result["exit_code"] != 0:
        return False, "non-zero exit", {"terms": seen}
    if terms and not any(seen.values()):
        return False, "none of the expected terms appeared", {"terms": seen}
    return True, "CLI output contained expected signal", {"terms": seen}


def jsonish_passed(result: dict[str, Any], terms: tuple[str, ...]) -> tuple[bool, str, dict[str, Any]]:
    output = result["stdout"] + "\n" + result["stderr"]
    seen = output_has_terms(output, terms)
    has_object = "{" in output and "}" in output
    has_array = "[" in output and "]" in output
    if result["exit_code"] != 0:
        return False, "non-zero exit", {"terms": seen, "has_object": has_object, "has_array": has_array}
    if re.search(r"\n\[\s*\]\s*$", output):
        return False, "empty JSON result", {"terms": seen, "has_object": has_object, "has_array": has_array}
    if not has_object:
        return False, "no structured object detected", {"terms": seen, "has_object": has_object, "has_array": has_array}
    if terms and not any(seen.values()):
        return True, "non-empty structured output; expected literal not present", {
            "terms": seen,
            "has_object": has_object,
            "has_array": has_array,
        }
    return True, "non-empty structured output with expected signal", {
        "terms": seen,
        "has_object": has_object,
        "has_array": has_array,
    }


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(item, ensure_ascii=False) + "\n")


def record(
    results_path: Path,
    *,
    site: Site | None,
    primitive: str,
    knob: Knob | None,
    command_result: dict[str, Any],
    passed: bool,
    reason: str,
    signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = {
        "site_id": site.site_id if site else None,
        "url": site.url if site else None,
        "purpose": site.purpose if site else None,
        "primitive": primitive,
        "mode": knob.mode if knob else None,
        "wait": knob.wait if knob else None,
        "hands_off_grade": knob.grade if knob and passed else None,
        "passed": passed,
        "reason": reason,
        "signals": signals or {},
        "command": command_result["command"],
        "duration_seconds": command_result["duration_seconds"],
        "exit_code": command_result["exit_code"],
        "timed_out": command_result["timed_out"],
        "stdout_path": command_result["stdout_path"],
        "stderr_path": command_result["stderr_path"],
    }
    append_jsonl(results_path, item)
    return item


def run_ladder(
    site: Site,
    primitive: str,
    out_dir: Path,
    results_path: Path,
    args_for_knob,
    evaluator,
    timeout: int,
) -> dict[str, Any]:
    last: dict[str, Any] | None = None
    for knob in site.knobs:
        slug = f"{site.site_id}_{primitive}_{knob.mode}_{knob.wait}"
        command_result = run_command(args_for_knob(knob), out_dir, slug, timeout=timeout)
        passed, reason, signals = evaluator(command_result, site)
        last = record(
            results_path,
            site=site,
            primitive=primitive,
            knob=knob,
            command_result=command_result,
            passed=passed,
            reason=reason,
            signals=signals,
        )
        if passed:
            return last
    assert last is not None
    last["hands_off_grade"] = "F"
    return last


def summarize(out_dir: Path, records: list[dict[str, Any]]) -> None:
    results_path = out_dir / "results.jsonl"
    if results_path.exists():
        records = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()]

    best_by_site: dict[str, dict[str, dict[str, Any]]] = {}
    for item in records:
        site_id = item.get("site_id") or "global"
        primitive = item["primitive"]
        best_by_site.setdefault(site_id, {})
        if primitive not in best_by_site[site_id] and item["passed"]:
            best_by_site[site_id][primitive] = item

    total = len(records)
    passed = sum(1 for item in records if item["passed"])
    failed = total - passed
    lines = [
        "# Atlas CLI Smoke Run",
        "",
        f"- Total CLI attempts: {total}",
        f"- Passed attempts: {passed}",
        f"- Failed attempts: {failed}",
        "",
        "## Best Passing Knobs",
        "",
        "| Site | Primitive | Grade | Mode | Wait | Reason |",
        "|---|---|---:|---|---|---|",
    ]
    for site_id in sorted(best_by_site):
        for primitive in ("scrape", "index", "schema", "extract", "search"):
            item = best_by_site[site_id].get(primitive)
            if not item:
                continue
            lines.append(
                "| {site} | {primitive} | {grade} | {mode} | {wait} | {reason} |".format(
                    site=site_id,
                    primitive=primitive,
                    grade=item.get("hands_off_grade") or "",
                    mode=item.get("mode") or "",
                    wait=item.get("wait") or "",
                    reason=item["reason"].replace("|", "\\|"),
                )
            )

    lines.extend(["", "## Failed Attempts", "", "| Site | Primitive | Mode | Wait | Reason |", "|---|---|---|---|---|"])
    for item in records:
        if item["passed"]:
            continue
        lines.append(
            "| {site} | {primitive} | {mode} | {wait} | {reason} |".format(
                site=item.get("site_id") or "global",
                primitive=item["primitive"],
                mode=item.get("mode") or "",
                wait=item.get("wait") or "",
                reason=item["reason"].replace("|", "\\|"),
            )
        )
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    current_run = run_id()
    out_dir = RUNS / current_run
    (out_dir / "stdout").mkdir(parents=True, exist_ok=True)
    (out_dir / "stderr").mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    records: list[dict[str, Any]] = []

    for query in SEARCH_QUERIES:
        slug = "search_" + re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")
        command_result = run_command(["search", query, "--max-results", "5"], out_dir, slug, timeout=180)
        passed, reason, signals = table_passed(command_result, Site("search", "", "global search", ""), ("http",))
        records.append(
            record(
                results_path,
                site=None,
                primitive="search",
                knob=None,
                command_result=command_result,
                passed=passed,
                reason=reason,
                signals=signals,
            )
        )

    for site in SITES:
        records.append(
            run_ladder(
                site,
                "scrape",
                out_dir,
                results_path,
                lambda knob, site=site: [
                    "scrape",
                    site.url,
                    "--mode",
                    knob.mode,
                    "--wait",
                    knob.wait,
                    "-f",
                    "html",
                    "-f",
                    "markdown",
                ],
                scrape_passed,
                timeout=240,
            )
        )
        records.append(
            run_ladder(
                site,
                "index",
                out_dir,
                results_path,
                lambda knob, site=site: [
                    "index",
                    site.url,
                    "--max-depth",
                    "0",
                    "--dedupe",
                    "--concurrency",
                    "1",
                    "--mode",
                    knob.mode,
                    "--wait",
                    knob.wait,
                ],
                lambda result, site=site: table_passed(result, site, site.index_terms),
                timeout=240,
            )
        )
        records.append(
            run_ladder(
                site,
                "schema",
                out_dir,
                results_path,
                lambda knob, site=site: [
                    "schema",
                    site.url,
                    "--prompt",
                    site.prompt,
                    "--mode",
                    knob.mode,
                    "--wait",
                    knob.wait,
                ],
                lambda result, site=site: jsonish_passed(result, ("baseSelector", "fields")),
                timeout=360,
            )
        )
        records.append(
            run_ladder(
                site,
                "extract",
                out_dir,
                results_path,
                lambda knob, site=site: [
                    "extract",
                    site.url,
                    "--prompt",
                    site.prompt,
                    "--mode",
                    knob.mode,
                    "--wait",
                    knob.wait,
                ],
                lambda result, site=site: jsonish_passed(result, site.must_contain),
                timeout=360,
            )
        )

    summarize(out_dir, records)
    print(out_dir)


if __name__ == "__main__":
    main()
