# Atlas CLI Smoke Run

- Total CLI attempts: 62
- Passed attempts: 61
- Failed attempts: 1

## Best Passing Knobs

| Site | Primitive | Grade | Mode | Wait | Reason |
|---|---|---:|---|---|---|
| books_to_scrape | scrape | A | static | none | scrape wrote non-empty artifacts |
| books_to_scrape | index | A | static | none | CLI output contained expected signal |
| books_to_scrape | extract | A | static | none | non-empty structured output; expected literal not present |
| example | scrape | A | static | none | scrape wrote non-empty artifacts |
| example | index | A | static | none | CLI output contained expected signal |
| example | extract | A | static | none | non-empty structured output with expected signal |
| global | search |  |  |  | CLI output contained expected signal |
| httpbin_html | scrape | A | static | none | scrape wrote non-empty artifacts |
| httpbin_html | index | A | static | none | CLI output contained expected signal |
| httpbin_html | extract | A | static | none | non-empty structured output with expected signal |
| mercari_jp | scrape | A | static | none | scrape wrote non-empty artifacts |
| mercari_jp | index | A | static | none | CLI output contained expected signal |
| mercari_jp | extract | A | static | none | non-empty structured output; expected literal not present |
| practice_expand | scrape | A | static | none | scrape wrote non-empty artifacts |
| practice_expand | index | A | static | none | CLI output contained expected signal |
| practice_expand | extract | A | static | none | non-empty structured output with expected signal |
| quotes_js | scrape | A | static | none | scrape wrote non-empty artifacts |
| quotes_js | index | A | static | none | CLI output contained expected signal |
| quotes_js | extract | A | static | none | non-empty structured output with expected signal |
| quotes_scroll | scrape | A | static | none | scrape wrote non-empty artifacts |
| quotes_scroll | index | A | static | none | CLI output contained expected signal |
| quotes_scroll | extract | B | dynamic | stable | non-empty structured output with expected signal |
| quotes_static | scrape | A | static | none | scrape wrote non-empty artifacts |
| quotes_static | index | A | static | none | CLI output contained expected signal |
| quotes_static | extract | A | static | none | non-empty structured output with expected signal |
| scrape_ajax | scrape | A | static | none | scrape wrote non-empty artifacts |
| scrape_ajax | index | A | static | none | CLI output contained expected signal |
| scrape_countries | scrape | A | static | none | scrape wrote non-empty artifacts |
| scrape_countries | index | A | static | none | CLI output contained expected signal |
| scrape_countries | extract | A | static | none | non-empty structured output with expected signal |
| the_internet_tables | scrape | A | static | none | scrape wrote non-empty artifacts |
| the_internet_tables | index | A | static | none | CLI output contained expected signal |
| the_internet_tables | extract | A | static | none | non-empty structured output with expected signal |
| webscraper_more | scrape | A | static | none | scrape wrote non-empty artifacts |
| webscraper_more | index | A | static | none | CLI output contained expected signal |
| webscraper_more | extract | A | static | none | non-empty structured output; expected literal not present |
| webscraper_scroll | scrape | A | static | none | scrape wrote non-empty artifacts |
| webscraper_scroll | index | A | static | none | CLI output contained expected signal |
| webscraper_scroll | extract | A | static | none | non-empty structured output; expected literal not present |
| webscraper_static | scrape | A | static | none | scrape wrote non-empty artifacts |
| webscraper_static | index | A | static | none | CLI output contained expected signal |
| webscraper_static | extract | A | static | none | non-empty structured output; expected literal not present |
| wikipedia_web_scraping | scrape | A | static | none | scrape wrote non-empty artifacts |
| wikipedia_web_scraping | index | A | static | none | CLI output contained expected signal |
| wikipedia_web_scraping | extract | A | static | none | non-empty structured output with expected signal |

## Failed Attempts

| Site | Primitive | Mode | Wait | Reason |
|---|---|---|---|---|
| scrape_ajax | extract | app | stable | empty JSON result |
