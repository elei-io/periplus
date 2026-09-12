"""Known claim rejection retains preparation; uncertain writes never retry here."""

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from periplus.materialization.batch import _dictionary_claim
from periplus.materialization.store import MaterializationRunStopped
from periplus.retention.identities import WriteClaimUnavailable


class PreparationClaimRetryTests(unittest.TestCase):
    def claim(self, *, active=False, check=None):
        return _dictionary_claim(
            Mock(),
            SimpleNamespace(id="generation"),
            active_generation=active,
            assert_writable=check,
            retained_bytes=1024,
        )

    def test_repeated_acquisition_rejection_retries_without_reentering_body(self):
        attempts = []
        releases = []

        @contextmanager
        def claims(*args, **kwargs):
            attempts.append(kwargs)
            if len(attempts) < 4:
                raise WriteClaimUnavailable("busy")
            try:
                yield
            finally:
                releases.append(True)

        check = Mock()
        with (
            patch("periplus.materialization.batch.write_claims", claims),
            patch("periplus.materialization.batch.time.sleep") as sleep,
        ):
            with self.claim(check=check) as current:
                self.assertTrue(current)
                self.assertEqual(len(attempts), 4)
                self.assertEqual(releases, [])
        self.assertEqual(releases, [True])
        self.assertEqual(check.call_count, 3)
        self.assertEqual(sleep.call_count, 3)
        self.assertTrue(all(a == {"wait_seconds": 0} for a in attempts))

    def test_body_exceptions_including_same_exception_type_are_not_retried(self):
        for error in (WriteClaimUnavailable("body"), RuntimeError("unknown outcome")):
            with self.subTest(error=type(error)):
                with (
                    patch("periplus.materialization.batch.write_claims") as claims,
                    patch("periplus.materialization.batch.time.sleep") as sleep,
                ):
                    with self.assertRaises(type(error)):
                        with self.claim():
                            raise error
                    self.assertEqual(claims.call_count, 1)
                    sleep.assert_not_called()

    def test_cancelled_generation_stops_wait_before_next_acquisition(self):
        with (
            patch("periplus.materialization.batch.write_claims") as claims,
            patch("periplus.materialization.batch.time.sleep"),
        ):
            claims.return_value.__enter__.side_effect = WriteClaimUnavailable("busy")
            check = Mock(side_effect=MaterializationRunStopped("cancelled"))
            with self.assertRaises(MaterializationRunStopped):
                with self.claim(check=check):
                    self.fail("cancelled preparation reached transaction")
            self.assertEqual(claims.call_count, 1)

    def test_live_generation_supersession_skips_reservation(self):
        with (
            patch(
                "periplus.materialization.batch._is_active_generation",
                return_value=False,
            ),
            patch("periplus.materialization.batch.write_claims") as claims,
        ):
            with self.claim(active=True) as current:
                self.assertFalse(current)
            claims.assert_not_called()

    def test_retention_deadline_bounds_retries(self):
        with (
            patch("periplus.materialization.batch.write_claims") as claims,
            patch(
                "periplus.materialization.batch.time.monotonic", side_effect=[0, 121]
            ),
            patch("periplus.materialization.batch.time.sleep") as sleep,
        ):
            claims.return_value.__enter__.side_effect = WriteClaimUnavailable("busy")
            with self.assertRaises(WriteClaimUnavailable):
                with self.claim():
                    self.fail("timed out preparation reached transaction")
            self.assertEqual(claims.call_count, 1)
            sleep.assert_not_called()
