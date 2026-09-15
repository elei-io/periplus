"""Instrumented benchmark storage. All writes are confined to a unique run prefix."""

from collections import Counter
from contextlib import contextmanager
from io import BytesIO
from datetime import UTC, datetime
import re
from threading import Lock

from periplus.ingestion.objects.store import (
    FileObjectStore,
    S3ObjectStore,
    ObjectMetadata,
)


class MeteredStore:
    def __init__(self, store):
        self.store = store
        self.calls = Counter()
        self.read_bytes = 0
        self.written_bytes = 0
        self.fault = None
        self._lock = Lock()

    def meter(self, operation=None, *, read=0, written=0):
        with self._lock:
            if operation:
                self.calls[operation] += 1
            self.read_bytes += read
            self.written_bytes += written

    def reset(self):
        self.calls.clear()
        self.read_bytes = self.written_bytes = 0

    def stats(self):
        return dict(
            requests=dict(self.calls),
            read_bytes=self.read_bytes,
            written_bytes=self.written_bytes,
        )

    def put_if_absent(self, key, content, *, headers=None):
        data = content.read()
        self.meter("put", written=len(data))
        result = self.store.put_if_absent(key, BytesIO(data), headers=headers)
        if self.fault:
            self.fault(key)
        return result

    @contextmanager
    def open(self, key):
        self.meter("get")
        with self.store.open(key) as stream:
            data = stream.read()
        self.meter(read=len(data))
        yield BytesIO(data)

    def range(self, key, start, length):
        self.meter("range_get")
        if isinstance(self.store, S3ObjectStore):
            response = self.store.client.get_object(
                Bucket=self.store.bucket,
                Key="/".join(x for x in (self.store.prefix, key) if x),
                Range=f"bytes={start}-{start + length - 1}",
            )
            with response["Body"] as stream:
                data = stream.read()
        else:
            with self.store.open(key) as stream:
                stream.seek(start)
                data = stream.read(length)
        if len(data) != length:
            raise ValueError("Short archive range")
        self.meter(read=len(data))
        return data

    def exists(self, key):
        self.meter("head")
        return self.store.exists(key)

    def size(self, key):
        self.meter("head")
        return self.store.size(key)

    def delete(self, key):
        self.meter("delete")
        return self.store.delete(key)

    def list_objects(self, prefix):
        self.meter("list")
        if prefix:
            yield from self.store.list_objects(prefix)
        elif isinstance(self.store, S3ObjectStore):
            root = self.store.prefix + "/"
            for page in self.store.client.get_paginator("list_objects_v2").paginate(
                Bucket=self.store.bucket, Prefix=root
            ):
                for item in page.get("Contents", []):
                    yield ObjectMetadata(
                        item["Key"][len(root) :], item["Size"], item["LastModified"]
                    )
        else:
            for path in self.store.root.rglob("*"):
                if path.is_file():
                    info = path.stat()
                    yield ObjectMetadata(
                        path.relative_to(self.store.root).as_posix(),
                        info.st_size,
                        datetime.fromtimestamp(info.st_mtime, UTC),
                    )

    def footprint(self):
        objects = list(self.list_objects(""))
        return dict(objects=len(objects), object_bytes=sum(o.size for o in objects))


def target_store(source, root: str, suffix: str):
    if not re.fullmatch(r"benchmarks/archive-layout-[0-9a-f]{32}", root):
        raise ValueError("Destination must be a fresh benchmark UUID prefix")
    if not re.fullmatch(r"[a-z0-9_]+(?:/[a-z0-9_]+)*", suffix):
        raise ValueError("Invalid benchmark suffix")
    if isinstance(source, S3ObjectStore):
        return MeteredStore(
            S3ObjectStore(
                source.client,
                bucket=source.bucket,
                prefix="/".join(x for x in (source.prefix, root, suffix) if x),
            )
        )
    return MeteredStore(FileObjectStore(source.root / root / suffix))


def read_all(store, key):
    with store.open(key) as stream:
        return stream.read()


def cleanup(store: MeteredStore):
    for item in list(store.list_objects("")):
        store.delete(item.key)
