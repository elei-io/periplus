#!/usr/bin/env python3
"""Create and visibly drive one disposable self-hosted Steel browser session."""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import suppress
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from playwright.async_api import async_playwright


def create_session(api_url: str) -> dict[str, object]:
    request = Request(
        f"{api_url.rstrip('/')}/v1/sessions",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "blockAds": True,
                "dimensions": {"width": 1280, "height": 800},
                "headless": False,
            }
        ).encode(),
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(
            f"Steel rejected session creation ({error.code}): {detail}"
        ) from error


def interactive_viewer_url(url: str) -> str:
    parts = urlsplit(url.replace("0.0.0.0", "127.0.0.1"))
    query = dict(parse_qsl(parts.query))
    query["interactive"] = "true"
    return urlunsplit((*parts[:3], urlencode(query), parts.fragment))


def release_session(api_url: str, session_id: str) -> None:
    request = Request(
        f"{api_url.rstrip('/')}/v1/sessions/{session_id}",
        method="DELETE",
    )
    with urlopen(request, timeout=10):
        pass


async def run(args: argparse.Namespace) -> None:
    session = await asyncio.to_thread(create_session, args.api_url)
    session_id = str(session["id"])
    websocket_url = str(session["websocketUrl"])
    viewer_url = str(session.get("debugUrl") or session["sessionViewerUrl"])
    viewer_url = interactive_viewer_url(viewer_url)
    websocket_url = websocket_url.replace("0.0.0.0", "127.0.0.1")

    print(f"Session: {session_id}", flush=True)
    print(f"Live view: {viewer_url}", flush=True)

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(websocket_url)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(args.url, wait_until="domcontentloaded")
            print(f"Loaded: {await page.title()}", flush=True)
            print(f"Holding the live session for {args.hold_seconds} seconds.", flush=True)
            await asyncio.sleep(args.hold_seconds)
            await browser.close()
    finally:
        with suppress(Exception):
            await asyncio.to_thread(release_session, args.api_url, session_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:3000")
    parser.add_argument(
        "--url",
        default="https://jp.mercari.com/en/search?keyword=16tb%20ironwolf",
    )
    parser.add_argument("--hold-seconds", type=int, default=300)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
