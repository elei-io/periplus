# Atlas CLI Smoke Run - Corrected Summary

- Total CLI attempts: 65
- Passed attempts: 61
- Failed attempts: 4

## Best Passing Knobs

| Site | Primitive | Grade | Mode | Wait | Attempts | Reason |
|---|---|---:|---|---|---:|---|
| books_to_scrape | extract | A | static | none | 1 | non-empty structured output; expected literal not present |
| books_to_scrape | index | A | static | none | 1 | CLI output contained expected signal |
| books_to_scrape | schema | A | static | none | 1 | non-empty structured output with expected signal |
| books_to_scrape | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |
| example | extract | A | static | none | 1 | non-empty structured output with expected signal |
| example | index | A | static | none | 1 | CLI output contained expected signal |
| example | schema | A | static | none | 1 | non-empty structured output with expected signal |
| example | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |
| global | search |  |  |  | 2 | CLI output contained expected signal |
| httpbin_html | extract | A | static | none | 1 | non-empty structured output with expected signal |
| httpbin_html | index | A | static | none | 1 | CLI output contained expected signal |
| httpbin_html | schema | A | static | none | 1 | non-empty structured output with expected signal |
| httpbin_html | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |
| mercari_jp | extract | A | static | none | 1 | non-empty structured output; expected literal not present |
| mercari_jp | index | A | static | none | 1 | CLI output contained expected signal |
| mercari_jp | schema | A | static | none | 1 | non-empty structured output with expected signal |
| mercari_jp | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |
| practice_expand | extract | A | static | none | 1 | non-empty structured output with expected signal |
| practice_expand | index | A | static | none | 1 | CLI output contained expected signal |
| practice_expand | schema | A | static | none | 1 | non-empty structured output with expected signal |
| practice_expand | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |
| quotes_js | extract | A | static | none | 1 | non-empty structured output with expected signal |
| quotes_js | index | A | static | none | 1 | CLI output contained expected signal |
| quotes_js | schema | A | static | none | 1 | non-empty structured output with expected signal |
| quotes_js | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |
| quotes_scroll | extract | B | dynamic | stable | 2 | non-empty structured output with expected signal |
| quotes_scroll | index | A | static | none | 1 | CLI output contained expected signal |
| quotes_scroll | schema | A | static | none | 1 | non-empty structured output with expected signal |
| quotes_scroll | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |
| quotes_static | extract | A | static | none | 1 | non-empty structured output with expected signal |
| quotes_static | index | A | static | none | 1 | CLI output contained expected signal |
| quotes_static | schema | A | static | none | 1 | non-empty structured output with expected signal |
| quotes_static | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |
| scrape_ajax | index | A | static | none | 1 | CLI output contained expected signal |
| scrape_ajax | schema | A | static | none | 1 | non-empty structured output with expected signal |
| scrape_ajax | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |
| scrape_countries | extract | A | static | none | 1 | non-empty structured output with expected signal |
| scrape_countries | index | A | static | none | 1 | CLI output contained expected signal |
| scrape_countries | schema | A | static | none | 1 | non-empty structured output with expected signal |
| scrape_countries | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |
| the_internet_tables | extract | A | static | none | 1 | non-empty structured output with expected signal |
| the_internet_tables | index | A | static | none | 1 | CLI output contained expected signal |
| the_internet_tables | schema | A | static | none | 1 | non-empty structured output with expected signal |
| the_internet_tables | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |
| webscraper_more | extract | A | static | none | 1 | non-empty structured output; expected literal not present |
| webscraper_more | index | A | static | none | 1 | CLI output contained expected signal |
| webscraper_more | schema | A | static | none | 1 | non-empty structured output with expected signal |
| webscraper_more | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |
| webscraper_scroll | extract | A | static | none | 1 | non-empty structured output; expected literal not present |
| webscraper_scroll | index | A | static | none | 1 | CLI output contained expected signal |
| webscraper_scroll | schema | A | static | none | 1 | non-empty structured output with expected signal |
| webscraper_scroll | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |
| webscraper_static | extract | A | static | none | 1 | non-empty structured output; expected literal not present |
| webscraper_static | index | A | static | none | 1 | CLI output contained expected signal |
| webscraper_static | schema | A | static | none | 1 | non-empty structured output with expected signal |
| webscraper_static | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |
| wikipedia_web_scraping | extract | A | static | none | 1 | non-empty structured output with expected signal |
| wikipedia_web_scraping | index | A | static | none | 1 | CLI output contained expected signal |
| wikipedia_web_scraping | schema | A | static | none | 1 | non-empty structured output with expected signal |
| wikipedia_web_scraping | scrape | A | static | none | 1 | scrape wrote non-empty artifacts |

## No Passing Configuration

| Site | Primitive | Attempts | Tried |
|---|---|---:|---|
| scrape_ajax | extract | 3 | static/none: empty JSON result, dynamic/stable: empty JSON result, app/stable: empty JSON result |

## Targeted Follow-Up

- Mercari root (`https://jp.mercari.com/`) passed with `static/none` for a lightweight nav/search/recommendation extraction, even with `--refresh-schema`.
- Mercari search (`https://jp.mercari.com/en/search?keyword=16tb%20ironwolf`) did not produce useful listing fields with `static/none`: it returned listing-shaped objects with empty `title`, `price`, and `status`.
- The same Mercari search URL passed with `app/stable`, returning populated titles, prices, image URLs, and detail URLs.
- Web Scraper load-more and infinite-scroll pages returned product records with `static/none`, but only the initially available/default batch was verified. That is a hands-off pass for basic extraction, not proof that every lazily loaded item was harvested.

## All Failed Attempts

| Site | Primitive | Mode | Wait | Reason |
|---|---|---|---|---|
| quotes_scroll | extract | static | none | empty JSON result |
| scrape_ajax | extract | static | none | empty JSON result |
| scrape_ajax | extract | dynamic | stable | empty JSON result |
| scrape_ajax | extract | app | stable | empty JSON result |
