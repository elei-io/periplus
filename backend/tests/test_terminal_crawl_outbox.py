from datetime import UTC, datetime
from types import SimpleNamespace
import unittest
from uuid import uuid4

from repository.ingestion.queue import IngestionJob
from runtime.graph_store import _enqueue_terminal_crawl


class _Session:
    def __init__(self) -> None:
        self.added = []

    def add(self, value) -> None:
        self.added.append(value)


class TerminalCrawlOutboxTests(unittest.TestCase):
    def test_terminal_transition_enqueues_exactly_one_crawl_job(self) -> None:
        now = datetime.now(UTC)
        run_id = uuid4()
        record = SimpleNamespace(
            id=run_id,
            graph_id=uuid4(),
            status="completed",
            completed_at=now,
            started_at=now,
            created_at=now,
            snapshot={"graph_id": str(uuid4()), "nodes": [], "edges": []},
            root_admission_cursor=1,
            trigger_urls=["https://example.com/"],
            crawl_limit_reached=False,
        )
        session = _Session()

        _enqueue_terminal_crawl(session, record)

        self.assertEqual(len(session.added), 1)
        outbox = session.added[0]
        self.assertEqual(outbox.subject, "atlas.catalogue.ingest")
        job = IngestionJob.model_validate(outbox.payload)
        self.assertEqual(job.kind, "crawl")
        self.assertEqual(job.identity, run_id)
        self.assertEqual(job.crawl.root_url_count, 1)
        self.assertEqual(job.crawl.stop_reason, "completed")


if __name__ == "__main__":
    unittest.main()
