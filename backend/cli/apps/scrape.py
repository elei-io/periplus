from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cli.progress import CrawlProgressRenderer
from domains.scrape.models import OutputFormat
from domains.scrape.service import scrape_sync as scrape_service

console = Console()
_OUTPUT_FORMATS: set[OutputFormat] = {"html", "markdown", "pdf"}


def _parse_output_formats(output_formats: list[str] | None) -> list[OutputFormat] | None:
    if output_formats is None:
        return None

    parsed_formats: list[OutputFormat] = []
    for output_format in output_formats:
        if output_format not in _OUTPUT_FORMATS:
            raise typer.BadParameter(
                f"unsupported output format {output_format!r}; choose html, markdown, or pdf"
            )

        parsed_formats.append(output_format)

    return parsed_formats


def scrape(
    urls: Annotated[list[str], typer.Argument(help="One or more URLs to scrape.")],
    download_images: Annotated[
        bool,
        typer.Option("--download-images", help="Download every image discovered on each scraped page."),
    ] = False,
    output_formats: Annotated[
        list[str] | None,
        typer.Option(
            "--output-format",
            "-f",
            help="Artifact format to write. Repeat for multiple formats.",
        ),
    ] = None,
) -> None:
    with CrawlProgressRenderer(console) as progress:
        output = scrape_service(
            urls=urls,
            download_images=download_images,
            output_formats=_parse_output_formats(output_formats),
            progress_callback=progress.callback,
        )

    table = Table(title="Scrape artifacts")
    table.add_column("URL")
    table.add_column("Success")
    table.add_column("Artifacts")
    table.add_column("Bytes")
    table.add_column("Cache Dir")

    for page in output.pages:
        table.add_row(
            page.url,
            "yes" if page.success else "no",
            str(len(page.artifacts)),
            str(sum(artifact.bytes for artifact in page.artifacts)),
            page.cache_dir,
        )

    if not output.pages:
        table.caption = "No pages scraped."

    console.print(table)
    console.print(
        f"Wrote {output.stats.artifacts} artifacts "
        f"({output.stats.cache_hits} cache hits, {output.stats.images_downloaded} images, "
        f"{output.stats.bytes_written} bytes) "
        f"to {output.cache_root}"
    )
