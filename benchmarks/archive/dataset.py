"""Frozen real capture selection and explicitly synthetic duplicate workloads."""

from collections import Counter
from datetime import datetime
import gzip
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID, uuid5, NAMESPACE_URL

from periplus.ingestion.captures import Capture, Payload, canonical


def load_inventory(path: Path) -> list[Capture]:
    result = []
    with gzip.open(path, "rt") as stream:
        for line in stream:
            row = json.loads(line)
            result.append(
                Capture(
                    capture_id=UUID(row["capture_id"]),
                    requested_url=row["requested_url"],
                    effective_url=row["effective_url"],
                    captured_at=datetime.fromisoformat(row["observed_at"]),
                    timestamp_precision="microsecond",
                    http_status=row["status_code"],
                    completeness="complete",
                    payload=Payload(
                        content_id=row["content_sha256"],
                        byte_length=row["content_bytes"],
                        object_key=row["object_key"],
                        stored_bytes=row["stored_bytes"],
                        storage_encoding=row["storage_encoding"],
                        representation=row["representation"],
                        media_type=row["detected_media_type"],
                        declared_media_type=row["declared_media_type"],
                        charset=row["charset"],
                    ),
                )
            )
    return result


def fingerprint(captures: list[Capture]) -> str:
    return sha256(
        canonical(sorted((str(c.capture_id), c.digest) for c in captures))
    ).hexdigest()


def describe(captures: list[Capture]) -> dict:
    bodies = {c.payload.content_id: c.payload for c in captures if c.payload}
    sizes = sorted(p.byte_length for p in bodies.values())
    return dict(
        captures=len(captures),
        unique_bodies=len(bodies),
        logical_bytes=sum(sizes),
        source_stored_bytes=sum(p.stored_bytes for p in bodies.values()),
        p50=sizes[len(sizes) // 2] if sizes else 0,
        p95=sizes[int(len(sizes) * 0.95)] if sizes else 0,
        maximum=max(sizes, default=0),
        above_32_mib=sum(s > 32 * 1024 * 1024 for s in sizes),
        fingerprint=fingerprint(captures),
    )


def select(
    captures: list[Capture], size: int, workload: str = "representative"
) -> list[Capture]:
    ordered = sorted(
        captures, key=lambda c: sha256(str(c.capture_id).encode()).digest()
    )
    if workload == "random":
        result = ordered[:size]
    elif workload == "representative":
        counts = Counter(c.payload.content_id for c in ordered)
        extremes = sorted(ordered, key=lambda c: c.payload.byte_length, reverse=True)[
            :20
        ]
        repeats = [c for c in ordered if counts[c.payload.content_id] > 1][
            : min(size // 10, 500)
        ]
        selected = {
            str(c.capture_id): c for c in [*extremes, *repeats, *ordered[:size]]
        }
        result = list(selected.values())[:size]
    elif workload == "unique":
        result = list({c.payload.content_id: c for c in reversed(ordered)}.values())[
            :size
        ]
    elif workload == "small":
        result = sorted(ordered, key=lambda c: c.payload.byte_length)[:size]
    elif workload == "repeated":
        source = select(ordered, max(1, size // 10), "unique")
        result = [
            source[i % len(source)].model_copy(
                update={
                    "capture_id": uuid5(
                        NAMESPACE_URL,
                        f"archive-bench-repeat:{i}:{source[i % len(source)].capture_id}",
                    )
                }
            )
            for i in range(size)
        ]
    else:
        raise ValueError(workload)
    # Stable shuffled capture order, common to every physical layout.
    return sorted(result, key=lambda c: sha256(str(c.capture_id).encode()).digest())
