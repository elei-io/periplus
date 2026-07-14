from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from control.crawl_policies.schemas import CrawlPolicySnapshot
from control.crawl_policies.templates import (
    CRAWL_POLICY_TEMPLATES,
    TEMPLATE_REGISTRY_VERSION,
    next_trial_policy_snapshot,
)
from runtime.graph_queue import get_graph_run, list_crawl_requests, new_graph_run
from runtime.graph_progress import initialize_run_progress
from runtime.graph_runs import admit_request
from tests.test_graph_runtime import FakeJetStream, FakeKV, policy_snapshot, snapshot


class PolicyTemplateTests(unittest.TestCase):
    def test_registry_is_a_ranked_acquisition_ladder(self) -> None:
        self.assertEqual(
            [value.name for value in CRAWL_POLICY_TEMPLATES],
            [
                "http_fast",
                "static_fast",
                "static_stable",
                "dynamic_scan",
                "dynamic_stable",
                "app_stable",
                "app_deep",
                "provider",
            ],
        )
        candidate = next_trial_policy_snapshot(
            policy_snapshot(), url="https://example.com/docs", provider_enabled=False
        )
        assert candidate is not None
        value, template = candidate
        self.assertEqual(template, "static_fast")
        frozen = CrawlPolicySnapshot.model_validate(value)
        self.assertEqual(frozen.origin, "system_trial")
        self.assertEqual(frozen.revision, TEMPLATE_REGISTRY_VERSION)
        self.assertEqual(frozen.config["profile"], "browser")
        self.assertEqual(frozen.config["config"]["cache"], {"mode": "refresh"})


class PolicyTrialAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_sample_is_published_without_becoming_graph_work(self) -> None:
        runs, requests, progress = FakeKV(), FakeKV(), FakeKV()
        jetstream = FakeJetStream()
        graph = snapshot()
        run = new_graph_run(graph, ["https://example.com"])
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)

        integers = {
            "ATLAS_POLICY_TRIAL_SAMPLER_VERSION": 3,
            "ATLAS_POLICY_TRIAL_MAX_IN_FLIGHT": 10,
            "ATLAS_GRAPH_MAX_REQUESTS_PER_RUN": 100,
            "ATLAS_GRAPH_MAX_RUN_SECONDS": 3600,
        }
        with (
            patch("runtime.graph_runs.get_float", return_value=1.0),
            patch("runtime.graph_runs.get_bool", return_value=False),
            patch(
                "runtime.graph_runs.get_int",
                side_effect=lambda name: integers[name],
            ),
            patch(
                "runtime.graph_runs.ensure_policy_trial_budget_storage",
                new=AsyncMock(return_value=SimpleNamespace()),
            ),
            patch(
                "runtime.graph_runs.reconcile_policy_trial_budget",
                new=AsyncMock(),
            ),
            patch(
                "runtime.graph_runs.reserve_policy_trial_slot",
                new=AsyncMock(return_value=True),
            ),
        ):
            use, admitted = await admit_request(
                runs=runs,
                requests=requests,
                progress=progress,
                jetstream=jetstream,
                run_id=run.id,
                node_id=graph.root_node_id,
                url="https://example.com/",
                policy_resolver=lambda _url: policy_snapshot(),
            )

        assert use is not None and admitted
        values = await list_crawl_requests(requests, graph_run_id=run.id)
        self.assertEqual(len(values), 2)
        sample = next(value for value in values if value.purpose == "sample")
        self.assertEqual(use.purpose, "use")
        self.assertEqual(use.trial, sample.trial)
        self.assertEqual(sample.parent_request_id, use.id)
        self.assertEqual(
            [subject for subject, _payload in jetstream.messages],
            ["atlas.graph.crawl.http", "atlas.graph.crawl.browser"],
        )
        current = await get_graph_run(runs, run.id)
        assert current is not None
        self.assertEqual(current.request_count, 1)
        self.assertEqual(current.pending_request_count, 1)
