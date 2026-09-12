"""Real spawned parsers must preserve every projection and ownership boundary."""

import hashlib
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import zstandard

from periplus.ingestion.objects.exceptions import RepositoryIntegrityError
from periplus.ingestion.objects.html import RawHtmlRepository, html_object_key
from periplus.ingestion.objects.store import FileObjectStore
from periplus.materialization.document_projection import (
    DocumentObservation,
    DocumentProjectionSource,
    build_visit_batch_context,
)
from periplus.materialization.preparation import DOCUMENT_BYTES, prepare_projections
from periplus.materialization.registry import PROJECTIONS


class ParallelPreparationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.repository = RawHtmlRepository(FileObjectStore(self.root))
        self.visits = []
        self.documents = []
        self.sources = []
        self.now = datetime(2026, 9, 12, tzinfo=UTC)

    def document(self, html, *, observations=1, encoding="zstd"):
        raw = html.encode() if isinstance(html, str) else html
        identity = hashlib.sha256(raw).hexdigest()
        key = html_object_key(identity) if encoding == "zstd" else f"exact/{identity}"
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            zstandard.ZstdCompressor().compress(raw) if encoding == "zstd" else raw
        )
        observed = []
        for index in range(observations):
            visit, document = str(uuid4()), str(uuid4())
            url = f"https://example.com/{index}/"
            self.visits.append((visit, document, url, self.now))
            self.documents.append((document, visit, identity, key, encoding, len(raw)))
            observed.append(DocumentObservation(visit, document, url, self.now))
        self.sources.append(
            DocumentProjectionSource(identity, key, encoding, len(raw), tuple(observed))
        )
        return identity

    def arguments(self, owned=None):
        return {
            "visits": tuple(self.visits),
            "documents": tuple(self.documents),
            "content_output_hashes": frozenset(s.content_sha256 for s in self.sources)
            if owned is None
            else owned,
        }

    def test_all_projections_equal_with_repeated_unowned_and_non_html_visits(self):
        owned = self.document(
            "<title>東京</title><p>mon<strong>key</strong> Straße 中文</p>"
            '<a href="next" rel="nofollow">Next</a>'
            '<script type="application/ld+json">{"a":null,"a":2}</script>',
            observations=2,
        )
        self.document('<a href="/elsewhere">unowned</a>')
        self.document(
            b'<meta charset="windows-1252"><p>caf\xe9</p><script type="application/ld+json">bad</script>',
            encoding="identity",
        )
        self.visits.append((str(uuid4()), None, "https://example.com/failed", self.now))
        kwargs = self.arguments(frozenset({owned, self.sources[-1].content_sha256}))
        old = build_visit_batch_context(self.repository, tuple(self.sources), **kwargs)
        dictionary_inputs = {
            s.name: s.rows(old) for s in PROJECTIONS if s.dictionary_key is not None
        }
        # Non-contiguous, non-local dictionary IDs catch accidental local-ID publication.
        ids = {
            s.name: {
                key: i * 13 + 71
                for i, key in enumerate(
                    dictionary_inputs[s.name][s.dictionary_key].to_pylist()
                )
            }
            for s in PROJECTIONS
            if s.dictionary_key is not None
        }
        old = replace(old, dictionary_ids=ids)
        with prepare_projections(
            self.repository, tuple(self.sources), processes=2, **kwargs
        ) as prepared:
            directories = prepared.directories
            for name, expected in dictionary_inputs.items():
                self.assertTrue(
                    prepared.dictionary_inputs()[name].equals(expected), name
                )
            for spec in PROJECTIONS:
                if spec.dictionary_key is not None:
                    continue
                expected = spec.rows(old).sort_by(
                    [(key, "ascending") for key in spec.identity_columns]
                )
                actual = prepared.rows(spec, ids).sort_by(
                    [(key, "ascending") for key in spec.identity_columns]
                )
                self.assertTrue(
                    actual.schema.equals(expected.schema, check_metadata=True),
                    spec.name,
                )
                self.assertTrue(actual.equals(expected), spec.name)
        self.assertTrue(all(not path.exists() for path in directories))

    def test_empty_and_non_html_only_batch(self):
        self.visits.append((str(uuid4()), None, "https://example.com/", self.now))
        with prepare_projections(
            self.repository, (), processes=2, **self.arguments()
        ) as prepared:
            for spec in PROJECTIONS:
                if spec.dictionary_key is None:
                    self.assertEqual(
                        prepared.rows(spec, {}).num_rows,
                        int(spec.name == "visit_readiness"),
                    )

    def test_size_mismatch_and_unreadable_objects_fail_before_publication(self):
        self.document("monkey")
        original = self.sources[0]
        for source, error in (
            (replace(original, content_bytes=1), ValueError),
            (replace(original, object_key="missing"), RepositoryIntegrityError),
        ):
            with (
                self.subTest(source=source.object_key),
                self.assertRaises(error),
                prepare_projections(
                    self.repository, (source,), processes=2, **self.arguments()
                ),
            ):
                self.fail("invalid source accepted")

    def test_oversized_documents_run_alone_and_hard_limit_is_explicit(self):
        self.document("<p>monkey</p>")
        with (
            patch("periplus.materialization.preparation.PREFETCH_BYTES", 1),
            prepare_projections(
                self.repository, tuple(self.sources), processes=2, **self.arguments()
            ) as prepared,
        ):
            self.assertEqual(prepared.dictionary_inputs()["term"].num_rows, 1)
        with (
            self.assertRaisesRegex(ValueError, "128 MiB"),
            prepare_projections(
                self.repository,
                (replace(self.sources[0], content_bytes=DOCUMENT_BYTES + 1),),
                processes=2,
                **self.arguments(),
            ),
        ):
            self.fail("hard limit ignored")

    def test_prefetch_reservations_cover_running_and_queued_jobs(self):
        import threading
        import time

        from periplus.materialization.preparation import PREFETCH_BYTES

        self.document("monkey")
        sizes = [PREFETCH_BYTES // 3] * 6 + [PREFETCH_BYTES + 1]
        sources = tuple(
            replace(
                self.sources[0],
                content_sha256=str(i),
                content_bytes=size,
                observations=(),
            )
            for i, size in enumerate(sizes)
        )
        lock = threading.Lock()
        running = []
        peak = [0]

        def project(_repository, source, _context, _directory, _pool):
            with lock:
                running.append(source.content_bytes)
                peak[0] = max(peak[0], len(running))
                self.assertTrue(sum(running) <= PREFETCH_BYTES or len(running) == 1)
                self.assertLessEqual(len(running), 4)
            time.sleep(0.02)
            with lock:
                running.remove(source.content_bytes)
            return 0

        with (
            patch("periplus.materialization.preparation._read_and_project", project),
            prepare_projections(
                self.repository,
                sources,
                processes=2,
                visits=(),
                documents=(),
                content_output_hashes=frozenset(),
            ),
        ):
            pass
        self.assertGreater(peak[0], 1)

    def test_output_limit_cleans_partial_files(self):
        from periplus.materialization.preparation import _project

        context = build_visit_batch_context(self.repository, ())
        path = self.root / "partial"
        with (
            patch("periplus.materialization.preparation.DOCUMENT_OUTPUT_BYTES", 1),
            self.assertRaisesRegex(ValueError, "Arrow output budget"),
        ):
            _project(context, path)


if __name__ == "__main__":
    unittest.main()
