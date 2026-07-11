import unittest

from repository.ingestion.queue import IngestionState


class IngestionQueueTests(unittest.TestCase):
    def test_ingestion_state_has_resolved_annotations(self) -> None:
        IngestionState.model_rebuild()
        self.assertTrue(IngestionState.__pydantic_complete__)


if __name__ == "__main__":
    unittest.main()
