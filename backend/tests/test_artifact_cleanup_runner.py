import os
from unittest import TestCase
from unittest.mock import Mock, patch

from artifacts.cleanup import artifact_cleanup_interval_seconds, run_artifact_cleanup_once
from artifacts.service import ArtifactCleanupResult


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class ArtifactCleanupRunnerTests(TestCase):
    def test_interval_prefers_seconds_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ARTIFACTS_CLEANUP_INTERVAL_SECONDS": "30",
                "ARTIFACTS_CLEANUP_INTERVAL": "60",
            },
        ):
            self.assertEqual(artifact_cleanup_interval_seconds(), 30)

    def test_interval_falls_back_to_legacy_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ARTIFACTS_CLEANUP_INTERVAL": "60",
            },
            clear=True,
        ):
            self.assertEqual(artifact_cleanup_interval_seconds(), 60)

    def test_run_once_invalidates_then_cleans_and_commits(self) -> None:
        session = _FakeSession()
        session_factory = Mock(return_value=session)

        with (
            patch("artifacts.cleanup.artifact_cache_age_seconds", return_value=3600),
            patch("artifacts.cleanup.artifact_cleanup_batch_size", return_value=25),
            patch("artifacts.cleanup.invalidate_expired_artifacts", return_value=3) as invalidate,
            patch(
                "artifacts.cleanup.cleanup_invalidated_artifacts",
                return_value=ArtifactCleanupResult(
                    rows_deleted=2,
                    files_deleted=2,
                    missing_files=0,
                    errors=0,
                ),
            ) as cleanup,
        ):
            result = run_artifact_cleanup_once(session_factory)

        invalidate.assert_called_once_with(session, max_age_seconds=3600)
        cleanup.assert_called_once_with(session=session, limit=25)
        self.assertTrue(session.committed)
        self.assertFalse(session.rolled_back)
        self.assertEqual(result.invalidated, 3)
        self.assertEqual(result.rows_deleted, 2)
        self.assertEqual(result.files_deleted, 2)
