from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from control.crawl_policies.schemas import CrawlPolicySnapshot
from control.crawl_policies.service import SEEDED_PROFILES, profile_config_hash
from runtime.graph_queue import get_graph_run, list_crawl_requests, new_graph_run
from runtime.graph_progress import initialize_run_progress
from runtime.graph_runs import _trial_for_request, admit_request
from tests.test_graph_runtime import FakeJetStream, FakeKV, policy_snapshot, snapshot


class CrawlProfileLadderTests(unittest.TestCase):
    def test_seeded_profiles_form_the_five_step_cost_ladder(self) -> None:
        self.assertEqual(
            [value["slug"] for value in SEEDED_PROFILES],
            ["direct", "rendered", "settled", "full-page", "interactive"],
        )
        self.assertEqual(
            [value["cost_rank"] for value in SEEDED_PROFILES],
            [10, 20, 30, 40, 50],
        )

    def test_trial_freezes_the_next_profile_with_a_fresh_cache(self) -> None:
        with (
            patch("runtime.graph_runs.get_float", return_value=1.0),
            patch("runtime.graph_runs.get_int", return_value=3),
        ):
            candidate = _trial_for_request(
                uuid4(),
                policy_snapshot(),
                "https://example.com/docs",
            )

        assert candidate is not None
        metadata, _request_id, value, transport = candidate
        frozen = CrawlPolicySnapshot.model_validate(value)
        self.assertEqual(metadata.candidate_profile_slug, "rendered")
        self.assertEqual(
            metadata.candidate_profile_config_hash,
            profile_config_hash({"mode": "static", "wait": "none"}),
        )
        self.assertEqual(frozen.origin, "system_trial")
        self.assertEqual(frozen.profile.slug, "rendered")
        self.assertEqual(frozen.profile.config["cache"], {"mode": "refresh"})
        self.assertEqual(transport, "browser")


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
            patch(
                "runtime.graph_runs.get_int",
                side_effect=lambda name: integers[name],
            ),
            patch(
                "runtime.graph_runs.ensure_policy_trial_budget_storage",
                new=AsyncMock(return_value=SimpleNamespace()),
            ),
            patch("runtime.graph_runs.reconcile_policy_trial_budget", new=AsyncMock()),
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
