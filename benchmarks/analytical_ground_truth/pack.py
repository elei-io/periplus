"""Deterministic product-market evidence and independent truth oracle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid5


PACK_ROOT = Path(__file__).resolve().parent
PACK_NAME = "atlas-analytical-ground-truth-v1"
SCENARIO_NAME = "product_market"
_IDENTITY_NAMESPACE = UUID("c05b52c4-e500-41b6-a37f-2dc95a59f201")
GRAPH_ID = uuid5(_IDENTITY_NAMESPACE, f"{PACK_NAME}:graph")
GRAPH_RUN_ID = uuid5(_IDENTITY_NAMESPACE, f"{PACK_NAME}:run")
GRAPH_NODE_ID = uuid5(_IDENTITY_NAMESPACE, f"{PACK_NAME}:node")
POLICY = {
    "accepted_content_types": ["text/html"],
    "analytical_ground_truth": {
        "pack": PACK_NAME,
        "scenario": SCENARIO_NAME,
    },
    "completion": {
        "wait_dynamic": {"enabled": True},
        "scroll": {"enabled": True, "max_iterations": 2},
    },
}
POLICY_JSON = json.dumps(POLICY, separators=(",", ":"), sort_keys=True)
POLICY_HASH = sha256(POLICY_JSON.encode()).hexdigest()
STEP_CONFIG = {"quiet_ms": 250, "timeout_ms": 3000}
STEP_CONFIG_HASH = sha256(
    json.dumps(STEP_CONFIG, separators=(",", ":"), sort_keys=True).encode()
).hexdigest()


@dataclass(frozen=True, slots=True)
class Product:
    index: int
    gtin: str
    name: str
    alias: str
    base_price_cents: int


@dataclass(frozen=True, slots=True)
class Observation:
    ordinal: int
    observation_index: int
    product: Product
    retailer_index: int
    requested_url: str
    captured_at: datetime
    crawl_id: UUID
    status_code: int
    outcome: Literal["success", "failed"]
    html: str | None
    title: str | None
    price_cents: int | None
    availability: str | None
    final_state: Literal["in_stock", "out_of_stock", "removed", "unknown"]

    @property
    def retailer_domain(self) -> str:
        return retailer_domain(self.retailer_index)

    @property
    def document_id(self) -> str | None:
        if self.html is None:
            return None
        return f"sha256:{sha256(self.html.encode()).hexdigest()}"


def manifest() -> dict[str, Any]:
    return _read_json(PACK_ROOT / "manifest.json")


def products() -> tuple[Product, ...]:
    source = _read_json(PACK_ROOT / "scenarios" / "product_market.json")
    return tuple(
        Product(
            index=index,
            gtin=value["gtin"],
            name=value["name"],
            alias=value["alias"],
            base_price_cents=value["base_price_cents"],
        )
        for index, value in enumerate(source["products"])
    )


def observations(*, filler_elements: int | None = None) -> tuple[Observation, ...]:
    config = manifest()
    filler_count = (
        config["default_filler_elements"]
        if filler_elements is None
        else filler_elements
    )
    if filler_count < 0 or filler_count > 10_000:
        raise ValueError("filler_elements must be between 0 and 10000")
    start = datetime.fromisoformat(config["start_at"])
    offsets = config["observation_day_offsets"]
    result: list[Observation] = []
    ordinal = 0
    for observation_index, day_offset in enumerate(offsets):
        for product in products():
            for retailer_index in retailer_indexes(product.index):
                captured_at = (
                    start
                    + timedelta(days=day_offset)
                    + timedelta(minutes=product.index * 12 + retailer_index)
                )
                result.append(
                    _observation(
                        ordinal=ordinal,
                        observation_index=observation_index,
                        product=product,
                        retailer_index=retailer_index,
                        captured_at=captured_at,
                        filler_elements=filler_count,
                    )
                )
                ordinal += 1
    return tuple(result)


def retailer_indexes(product_index: int) -> tuple[int, ...]:
    return tuple(sorted({(product_index + offset) % 12 for offset in (0, 3, 6, 9)}))


def retailer_domain(retailer_index: int) -> str:
    return f"atlas-retailer-{retailer_index + 1:02d}.com"


def expected_rows(
    source: tuple[Observation, ...] | None = None,
) -> list[dict[str, Any]]:
    source = source or observations()
    histories: dict[tuple[str, int], list[Observation]] = {}
    for item in source:
        histories.setdefault((item.product.gtin, item.retailer_index), []).append(item)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for (gtin, retailer_index), history in histories.items():
        ordered = sorted(history, key=lambda value: value.captured_at)
        latest = ordered[-1]
        latest_offer = next(
            value
            for value in reversed(ordered)
            if value.price_cents is not None
        )
        grouped.setdefault(gtin, []).append(
            {
                "retailer_index": retailer_index,
                "retailer_domain": retailer_domain(retailer_index),
                "state": latest.final_state,
                "crawl_id": str(latest.crawl_id),
                "document_id": latest_offer.document_id,
                "price_cents": latest_offer.price_cents,
            }
        )

    rows: list[dict[str, Any]] = []
    for gtin, offers in sorted(grouped.items()):
        offers.sort(key=lambda value: value["retailer_domain"])
        in_stock = [value for value in offers if value["state"] == "in_stock"]
        cheapest = min(
            in_stock,
            key=lambda value: (
                value["price_cents"],
                value["retailer_domain"],
            ),
        )
        rows.append(
            {
                "gtin": gtin,
                "retailer_count": len(offers),
                "in_stock_count": _state_count(offers, "in_stock"),
                "out_of_stock_count": _state_count(offers, "out_of_stock"),
                "removed_count": _state_count(offers, "removed"),
                "unknown_count": _state_count(offers, "unknown"),
                "cheapest_price": f"{Decimal(cheapest['price_cents']) / 100:.2f}",
                "cheapest_retailer": cheapest["retailer_domain"],
                "evidence_crawl_ids": [
                    value["crawl_id"] for value in offers
                ],
                "evidence_document_ids": [
                    value["document_id"] for value in offers
                ],
            }
        )
    return rows


def expected_counts(
    source: tuple[Observation, ...] | None = None,
) -> dict[str, int]:
    source = source or observations()
    html_by_document_id = {
        item.document_id: item.html
        for item in source
        if item.document_id is not None and item.html is not None
    }
    from dom import iter_html_elements

    attempt_count = sum(attempts_for(item) for item in source)
    step_count = sum(
        0 if item.outcome == "failed" else 1
        for item in source
    )
    return {
        "crawls": len(source),
        "documents": len(html_by_document_id),
        "elements": sum(
            sum(1 for _ in iter_html_elements(html))
            for html in html_by_document_id.values()
        ),
        "crawl_attempts": attempt_count,
        "crawl_steps": step_count,
    }


def attempts_for(item: Observation) -> int:
    if item.outcome == "failed":
        return 1
    return 2 if (
        item.product.index * 13
        + item.retailer_index * 7
        + item.observation_index
    ) % 23 == 0 else 1


def normalized_result_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        normalized.append(
            {
                "gtin": str(row["gtin"]),
                "retailer_count": int(row["retailer_count"]),
                "in_stock_count": int(row["in_stock_count"]),
                "out_of_stock_count": int(row["out_of_stock_count"]),
                "removed_count": int(row["removed_count"]),
                "unknown_count": int(row["unknown_count"]),
                "cheapest_price": f"{Decimal(str(row['cheapest_price'])):.2f}",
                "cheapest_retailer": str(row["cheapest_retailer"]),
                "evidence_crawl_ids": [
                    str(value) for value in row["evidence_crawl_ids"]
                ],
                "evidence_document_ids": [
                    None if value is None else str(value)
                    for value in row["evidence_document_ids"]
                ],
            }
        )
    return normalized


def _observation(
    *,
    ordinal: int,
    observation_index: int,
    product: Product,
    retailer_index: int,
    captured_at: datetime,
    filler_elements: int,
) -> Observation:
    url = (
        f"https://{retailer_domain(retailer_index)}/products/"
        f"{product.gtin}?src=catalogue"
    )
    crawl_id = uuid5(
        _IDENTITY_NAMESPACE,
        (
            f"{PACK_NAME}:{SCENARIO_NAME}:{product.gtin}:"
            f"{retailer_index}:{observation_index}"
        ),
    )
    final_observation = observation_index == 9
    removed = final_observation and (
        product.index * 5 + retailer_index
    ) % 17 == 0
    unknown = (
        final_observation
        and not removed
        and (product.index * 7 + retailer_index) % 19 == 0
    )
    if unknown:
        return Observation(
            ordinal=ordinal,
            observation_index=observation_index,
            product=product,
            retailer_index=retailer_index,
            requested_url=url,
            captured_at=captured_at,
            crawl_id=crawl_id,
            status_code=503,
            outcome="failed",
            html=None,
            title=None,
            price_cents=None,
            availability=None,
            final_state="unknown",
        )
    if removed:
        html = _render_tombstone(
            product=product,
            retailer_index=retailer_index,
            filler_elements=filler_elements,
        )
        return Observation(
            ordinal=ordinal,
            observation_index=observation_index,
            product=product,
            retailer_index=retailer_index,
            requested_url=url,
            captured_at=captured_at,
            crawl_id=crawl_id,
            status_code=410,
            outcome="success",
            html=html,
            title=None,
            price_cents=None,
            availability=None,
            final_state="removed",
        )

    epoch = observation_index // 3
    title = (
        product.alias
        if observation_index >= 6 and product.index % 4 == 0
        else product.name
    )
    price_cents = (
        product.base_price_cents
        + retailer_index * 37
        + (product.index * retailer_index % 5) * 19
        + (0, -50, 75, -25)[epoch]
    )
    in_stock = (product.index + retailer_index + epoch) % 6 != 0
    availability = (
        "https://schema.org/InStock"
        if in_stock
        else "https://schema.org/OutOfStock"
    )
    html = _render_product(
        product=product,
        retailer_index=retailer_index,
        title=title,
        price_cents=price_cents,
        availability=availability,
        filler_elements=filler_elements,
    )
    return Observation(
        ordinal=ordinal,
        observation_index=observation_index,
        product=product,
        retailer_index=retailer_index,
        requested_url=url,
        captured_at=captured_at,
        crawl_id=crawl_id,
        status_code=200,
        outcome="success",
        html=html,
        title=title,
        price_cents=price_cents,
        availability=availability,
        final_state="in_stock" if in_stock else "out_of_stock",
    )


def _render_product(
    *,
    product: Product,
    retailer_index: int,
    title: str,
    price_cents: int,
    availability: str,
    filler_elements: int,
) -> str:
    template = (
        PACK_ROOT / "templates" / "product_page.html"
    ).read_text(encoding="utf-8")
    price = f"{Decimal(price_cents) / 100:.2f}"
    product_json = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "Product",
            "gtin13": product.gtin,
            "name": title,
            "sku": f"R{retailer_index + 1:02d}-{product.index + 1:04d}",
            "offers": {
                "@type": "Offer",
                "availability": availability,
                "price": price,
                "priceCurrency": "USD",
                "url": (
                    f"https://{retailer_domain(retailer_index)}/products/"
                    f"{product.gtin}?src=catalogue"
                ),
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return template.format(
        title=title,
        product_json=product_json,
        sku=f"R{retailer_index + 1:02d}-{product.index + 1:04d}",
        currency="USD",
        price=price,
        availability_label=availability.rsplit("/", 1)[-1],
        filler=_filler(product, retailer_index, filler_elements),
        retailer_number=retailer_index + 1,
    )


def _render_tombstone(
    *,
    product: Product,
    retailer_index: int,
    filler_elements: int,
) -> str:
    template = (
        PACK_ROOT / "templates" / "tombstone.html"
    ).read_text(encoding="utf-8")
    return template.format(
        filler=_filler(product, retailer_index, filler_elements)
    )


def _filler(product: Product, retailer_index: int, count: int) -> str:
    return "\n".join(
        (
            f'<section class="recommendation" data-slot="{index}">'
            f"<h2>Related category {index % 7}</h2>"
            f"<p>Retailer {retailer_index + 1} catalogue copy "
            f"{(product.index * 31 + index * 17) % 997}</p></section>"
        )
        for index in range(count)
    )


def _state_count(offers: list[dict[str, Any]], state: str) -> int:
    return sum(value["state"] == state for value in offers)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
