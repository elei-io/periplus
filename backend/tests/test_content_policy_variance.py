from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import uuid4

from atlas.crawl.control.content_policies.schemas import (
    ContentCompletion,
    ContentPolicyConfig,
    ContentPolicySnapshot,
    ExpandCompletion,
    ScrollCompletion,
    WaitDynamicCompletion,
    WaitFixedCompletion,
)
from atlas.crawl.control.content_policies.variance import vary_content_policy
from atlas.crawl.control.domain_policies.schemas import DomainPolicySnapshot
from atlas.crawl.runtime.graph_runs import resolve_policy_snapshot


class IndexedRandom:
    def __init__(self, *values: int) -> None:
        self.values = iter(values)

    def randint(self, a: int, b: int) -> int:
        value = next(self.values)
        if not a <= value <= b:
            raise AssertionError(f"{value} is outside [{a}, {b}]")
        return value


class ContentPolicyVarianceTests(unittest.TestCase):
    def test_default_policy_uses_discrete_lower_and_higher_arms(self) -> None:
        expected = (
            ("wait_dynamic.maximum_wait_ms", 6_000, 10_000),
            ("wait_dynamic.stable_samples", 2, 4),
            ("scroll.maximum_iterations", 23, 38),
            ("scroll.wait_ms", 125, 375),
            ("scroll.stable_bottom_samples", 2, 4),
            ("expand.maximum_actions", 8, 13),
            ("expand.wait_ms", 250, 750),
        )
        content = ContentPolicyConfig()

        for setting_index, (setting, lower, higher) in enumerate(expected):
            with self.subTest(setting=setting, arm="lower"):
                varied, assignment = vary_content_policy(
                    content,
                    rng=IndexedRandom(setting_index, 0),
                )
                self.assertIsNotNone(assignment)
                assert assignment is not None
                self.assertEqual(assignment.setting, setting)
                self.assertEqual(assignment.arm, "lower")
                self.assertEqual(assignment.effective_value, lower)
                self.assertEqual(
                    self._changed_settings(content, varied),
                    {setting: lower},
                )
            with self.subTest(setting=setting, arm="higher"):
                varied, assignment = vary_content_policy(
                    content,
                    rng=IndexedRandom(setting_index, 2),
                )
                self.assertIsNotNone(assignment)
                assert assignment is not None
                self.assertEqual(assignment.setting, setting)
                self.assertEqual(assignment.arm, "higher")
                self.assertEqual(assignment.effective_value, higher)
                self.assertEqual(
                    self._changed_settings(content, varied),
                    {setting: higher},
                )

    def test_baseline_arm_records_assignment_without_changing_policy(self) -> None:
        content = ContentPolicyConfig()

        varied, assignment = vary_content_policy(
            content,
            rng=IndexedRandom(2, 1),
        )

        self.assertEqual(varied, content)
        self.assertIsNotNone(assignment)
        assert assignment is not None
        self.assertEqual(assignment.setting, "scroll.maximum_iterations")
        self.assertEqual(assignment.arm, "baseline")
        self.assertEqual(assignment.configured_value, 30)
        self.assertEqual(assignment.effective_value, 30)

    def test_enabled_fixed_wait_is_eligible_for_variance(self) -> None:
        content = ContentPolicyConfig(
            completion=ContentCompletion(
                wait_dynamic=WaitDynamicCompletion(enabled=False),
                wait_fixed=WaitFixedCompletion(enabled=True, duration_ms=1_000),
                scroll=ScrollCompletion(enabled=False),
                expand=ExpandCompletion(enabled=False),
            )
        )

        varied, assignment = vary_content_policy(
            content,
            rng=IndexedRandom(0, 0),
        )

        self.assertEqual(varied.completion.wait_fixed.duration_ms, 500)
        self.assertIsNotNone(assignment)
        assert assignment is not None
        self.assertEqual(assignment.setting, "wait_fixed.duration_ms")
        self.assertEqual(assignment.arm, "lower")

    def test_disabled_methods_and_excluded_settings_remain_unchanged(self) -> None:
        content = ContentPolicyConfig(
            completion=ContentCompletion(
                wait_dynamic=WaitDynamicCompletion(
                    sample_interval_ms=321,
                ),
                wait_fixed=WaitFixedCompletion(enabled=False, duration_ms=999),
                scroll=ScrollCompletion(
                    enabled=False,
                    maximum_iterations=17,
                    viewport_ratio=0.6,
                    wait_ms=123,
                    stable_bottom_samples=5,
                ),
                expand=ExpandCompletion(
                    enabled=False,
                    maximum_actions=8,
                    wait_ms=234,
                ),
            )
        )

        varied, assignment = vary_content_policy(
            content,
            rng=IndexedRandom(0, 2),
        )

        self.assertEqual(varied.completion.navigation, content.completion.navigation)
        self.assertEqual(
            varied.completion.wait_dynamic.sample_interval_ms,
            content.completion.wait_dynamic.sample_interval_ms,
        )
        self.assertEqual(varied.completion.wait_fixed, content.completion.wait_fixed)
        self.assertEqual(varied.completion.scroll, content.completion.scroll)
        self.assertEqual(varied.completion.expand, content.completion.expand)
        self.assertIsNotNone(assignment)
        assert assignment is not None
        self.assertEqual(assignment.setting, "wait_dynamic.maximum_wait_ms")

    def test_no_active_experiment_returns_original_policy(self) -> None:
        content = ContentPolicyConfig(
            completion=ContentCompletion(
                wait_dynamic=WaitDynamicCompletion(enabled=False),
                wait_fixed=WaitFixedCompletion(enabled=False),
                scroll=ScrollCompletion(enabled=False),
                expand=ExpandCompletion(enabled=False),
            )
        )

        varied, assignment = vary_content_policy(content, rng=IndexedRandom())

        self.assertIs(varied, content)
        self.assertIsNone(assignment)

    def test_resolution_freezes_assignment_without_changing_domain_policy(self) -> None:
        content_policy = ContentPolicySnapshot(
            id=uuid4(),
            slug="content",
            scheme="*",
            host="*",
            path_prefix="/",
            path_mode="prefix",
        )
        domain = DomainPolicySnapshot(
            id=uuid4(),
            slug="domain",
            host_match="*",
            maximum_concurrency=8,
            minimum_request_interval_seconds=0,
        )
        varied_content, assignment = vary_content_policy(
            content_policy.content,
            rng=IndexedRandom(0, 0),
        )

        with (
            patch(
                "atlas.crawl.runtime.graph_runs.find_content_policies_for_urls",
                return_value={"https://example.com/": object()},
            ),
            patch(
                "atlas.crawl.runtime.graph_runs.find_domain_policies_for_urls",
                return_value={"https://example.com/": object()},
            ),
            patch(
                "atlas.crawl.runtime.graph_runs.content_policy_snapshot",
                return_value=content_policy,
            ),
            patch("atlas.crawl.runtime.graph_runs.domain_policy_snapshot", return_value=domain),
            patch(
                "atlas.crawl.runtime.graph_runs.vary_content_policy",
                return_value=(varied_content, assignment),
            ),
        ):
            resolved = resolve_policy_snapshot(object(), "https://example.com/")

        self.assertEqual(
            resolved["content"]["content"],
            varied_content.model_dump(mode="json"),
        )
        self.assertEqual(
            resolved["content"]["content_variance"],
            assignment.model_dump(mode="json") if assignment else None,
        )
        self.assertEqual(
            resolved["domain"],
            domain.model_dump(mode="json"),
        )

    @staticmethod
    def _changed_settings(
        before: ContentPolicyConfig,
        after: ContentPolicyConfig,
    ) -> dict[str, int]:
        before_completion = before.completion.model_dump(mode="python")
        after_completion = after.completion.model_dump(mode="python")
        changed = {}
        for method, before_values in before_completion.items():
            after_values = after_completion[method]
            for field, before_value in before_values.items():
                after_value = after_values[field]
                if after_value != before_value:
                    changed[f"{method}.{field}"] = after_value
        return changed


if __name__ == "__main__":
    unittest.main()
