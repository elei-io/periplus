"""Render deployment contracts, including autoscaler ownership and credentials."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
ROLES = ("api", "admin", "public", "crawler", "ingestor", "materializer", "query")


@unittest.skipUnless(shutil.which("helm"), "Helm is required for chart contract tests")
class HelmScalingTests(unittest.TestCase):
    def render(self, values=None, *, valid=True):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.yaml"
            path.write_text(yaml.safe_dump(values or {}))
            result = subprocess.run(["helm", "template", "scaling-test", str(ROOT / "charts/periplus"), "--namespace", "scaling-ns", "-f", str(path)], capture_output=True, text=True)
        if not valid:
            self.assertNotEqual(result.returncode, 0)
            return result.stderr
        self.assertEqual(result.returncode, 0, result.stderr)
        return [document for document in yaml.safe_load_all(result.stdout) if document]

    def test_operational_replication_is_shared_by_core_roles(self):
        for replicas in (1, 3, 5):
            docs = self.render({"config": {"nats": {"operationalReplicas": replicas}}})
            for role in ("api", "crawler", "ingestor", "materializer", "janitor", "setup"):
                doc = next(d for d in docs if d["kind"] in ("Deployment", "Job") and d["metadata"]["name"].endswith("-" + role))
                env = {v["name"]: v.get("value") for v in doc["spec"]["template"]["spec"]["containers"][0]["env"]}
                self.assertEqual(env["PERIPLUS_NATS_OPERATIONAL_REPLICAS"], str(replicas))
        for invalid in (0, 6, "three"):
            self.render({"config": {"nats": {"operationalReplicas": invalid}}}, valid=False)

    def test_default_is_installable_without_autoscaling_crds(self):
        documents = self.render()
        self.assertFalse(any(d["kind"] in ("ScaledObject", "PodMonitor") for d in documents))
        deployments = [d for d in documents if d["kind"] == "Deployment"]
        self.assertEqual(len(deployments), 8)
        for deployment in deployments:
            self.assertIn("replicas", deployment["spec"])

    def test_all_roles_have_bounded_ownership_and_scoped_metrics(self):
        values = {role: {"autoscaling": {"enabled": True}} for role in ROLES}
        values.update(autoscaling={"prometheus": {"serverAddress": "http://prometheus:9090"}}, podMonitor={"enabled": True})
        documents = self.render(values)
        scalers = [d for d in documents if d["kind"] == "ScaledObject"]
        self.assertEqual(len(scalers), len(ROLES))
        for scaler in scalers:
            spec = scaler["spec"]
            deployment = next(d for d in documents if d["kind"] == "Deployment" and d["metadata"]["name"] == spec["scaleTargetRef"]["name"])
            self.assertNotIn("replicas", deployment["spec"])
            self.assertEqual(spec["minReplicaCount"], 2)
            self.assertLessEqual(spec["maxReplicaCount"], 10)
            self.assertEqual(spec["advanced"]["horizontalPodAutoscalerConfig"]["behavior"]["scaleDown"]["stabilizationWindowSeconds"], 300)
            trigger = spec["triggers"][0]
            if trigger["type"] == "prometheus":
                self.assertEqual(trigger["metricType"], "AverageValue")
                self.assertEqual(trigger["metadata"]["ignoreNullValues"], "false")
                query = trigger["metadata"]["query"]
                self.assertIn('namespace="scaling-ns"', query)
                self.assertIn('periplus_release="scaling-test"', query)
                self.assertIn('time() - 60', query)
                self.assertNotIn('or vector(0)', query)
        monitors = [d for d in documents if d["kind"] == "PodMonitor"]
        self.assertEqual(len(monitors), 6)
        for monitor in monitors:
            labels = {r['targetLabel']: r['replacement'] for r in monitor['spec']['podMetricsEndpoints'][0]['relabelings']}
            self.assertEqual(labels['namespace'], 'scaling-ns')
            self.assertEqual(labels['periplus_release'], 'scaling-test')

    def test_resource_and_duckdb_overrides_are_role_local(self):
        docs = self.render({"query": {"duckdb": {"threads": 4, "memoryLimit": "2GB", "maxTempDirectorySize": "1GB"}, "resources": {"limits": {"cpu": "4", "memory": "4Gi", "ephemeral-storage": "3Gi"}}}})
        for role in ("api", "crawler", "ingestor", "materializer", "query", "janitor", "setup"):
            doc = next(d for d in docs if d['kind'] in ('Deployment', 'Job') and d['metadata']['name'].endswith('-'+role))
            container = doc['spec']['template']['spec']['containers'][0]
            env = {item['name']: item.get('value') for item in container['env']}
            self.assertIn('PERIPLUS_DUCKDB_MEMORY_LIMIT', env)
            if role == 'query':
                self.assertEqual(env['PERIPLUS_DUCKDB_THREADS'], '4')
                self.assertEqual(env['PERIPLUS_DUCKDB_MEMORY_LIMIT'], '2GB')
                self.assertEqual(env['PERIPLUS_DUCKDB_MAX_TEMP_DIRECTORY_SIZE'], '1GB')
                self.assertEqual(container['resources']['limits']['ephemeral-storage'], '3Gi')
                self.assertNotIn('PERIPLUS_CONTROL_DATABASE_URL', env)
                self.assertNotIn('PERIPLUS_NATS_URL', env)
                self.assertNotIn('envFrom', container)
            else:
                self.assertNotEqual(env['PERIPLUS_DUCKDB_MEMORY_LIMIT'], '2GB')

    def test_invalid_limits_and_missing_dependencies_fail_render(self):
        for values in (
            {'query': {'autoscaling': {'enabled': True}}},
            {'query': {'autoscaling': {'minReplicas': 0}}},
            {'api': {'autoscaling': {'enabled': True, 'minReplicas': 5, 'maxReplicas': 2}}},
            {'api': {'autoscaling': {'enabled': True}, 'resources': {'requests': {'cpu': None}}}},
            {'api': {'autoscaling': {'threshold': '0.5'}}},
            {'query': {'duckdb': {'memoryLimit': 'unlimited'}}},
            {'ingestor': {'concurrency': 5}},
            {'janitor': {'autoscaling': {'enabled': True}}},
            {'query': {'duckdb': {'threads': 0}}},
        ):
            with self.subTest(values=values):
                self.render(values, valid=False)

    def test_custom_query_and_behavior(self):
        docs = self.render({'api': {'autoscaling': {'enabled': True, 'metric': 'prometheus', 'query': 'sum(custom{namespace="{{ .Release.Namespace }}"})', 'behavior': {'scaleDown': {'stabilizationWindowSeconds': 600}}}}, 'autoscaling': {'prometheus': {'serverAddress': 'https://prometheus', 'authenticationRef': {'name': 'metrics-auth'}, 'metadata': {'authModes': 'bearer'}}}})
        scaler = next(d for d in docs if d['kind'] == 'ScaledObject')
        trigger = scaler['spec']['triggers'][0]
        self.assertEqual(trigger['metadata']['query'], 'sum(custom{namespace="scaling-ns"})')
        self.assertEqual(trigger['authenticationRef'], {'name': 'metrics-auth'})
        self.assertEqual(scaler['spec']['advanced']['horizontalPodAutoscalerConfig']['behavior']['scaleDown']['stabilizationWindowSeconds'], 600)

    @unittest.skipUnless(shutil.which("promtool"), "promtool is required for PromQL execution tests")
    def test_queries_deduplicate_queue_reports_and_reject_missing_or_stale_data(self):
        values = {role: {"autoscaling": {"enabled": True}} for role in ROLES}
        values["autoscaling"] = {"prometheus": {"serverAddress": "http://prometheus:9090"}}
        scalers = [d for d in self.render(values) if d["kind"] == "ScaledObject"]
        tests = []
        for scaler in scalers:
            trigger = scaler["spec"]["triggers"][0]
            if trigger["type"] != "prometheus":
                continue
            role = scaler["metadata"]["labels"]["app.kubernetes.io/component"]
            query = trigger["metadata"]["query"]
            series = []
            def add(metric, values, extra=""):
                for pod in ("a", "b"):
                    labels = f'namespace="scaling-ns",periplus_release="scaling-test",periplus_component="{role}",pod="{pod}"' + extra
                    series.append({"series": metric + "{" + labels + "}", "values": values})
            if role == "ingestor":
                add("periplus_repository_ingestion_jobs_pending", "300+0x8")
                add("periplus_repository_ingestion_jobs_ack_pending", "100+0x8")
                add("periplus_repository_ingestion_queue_observed_timestamp_seconds", "0+15x8")
                expected = 400
            elif role == "materializer":
                add("periplus_materialization_queue_messages", "6+0x8", ',state="pending"')
                add("periplus_materialization_queue_messages", "2+0x8", ',state="ack_pending"')
                add("periplus_materialization_queue_observed_timestamp_seconds", "0+15x8")
                expected = 8
            else:
                add("periplus_crawler_active_captures" if role == "crawler" else "periplus_query_active_operations", "4+0x8" if role == "crawler" else "1+0x8")
                expected = 8 if role == "crawler" else 2
            for name, inputs, at, samples in (
                ("demand", series, "2m", [{"labels": "{}", "value": expected}]),
                ("missing", [], "2m", []),
                ("stale", series, "4m", []),
                ("other release", [dict(item, series=item["series"].replace('periplus_release="scaling-test"', 'periplus_release="another"')) for item in series], "2m", []),
            ):
                tests.append({"name": role + " " + name, "interval": "15s", "input_series": inputs, "promql_expr_test": [{"expr": query, "eval_time": at, "exp_samples": samples}]})
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "tests.yaml").write_text(yaml.safe_dump({"rule_files": [], "evaluation_interval": "15s", "tests": tests}))
            result = subprocess.run(["promtool", "test", "rules", "tests.yaml"], cwd=directory, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
