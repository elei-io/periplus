"""A failed or replayed Helm upgrade must never mix old workers with setup."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[3] / 'charts/periplus/files/upgrade.py'
spec = importlib.util.spec_from_file_location('upgrade', PATH)
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


class Cluster:
    def __init__(self):
        self.deployments = {name: {'metadata': {'generation': 1, 'labels': {'app.kubernetes.io/instance': 'test'}},
                                  'spec': {'replicas': 2, 'template': {'spec': {'containers': [{'image': 'new'}]}}},
                                  'status': {'observedGeneration': 1, 'updatedReplicas': 2, 'availableReplicas': 2}}
                            for name in ('test-api', 'test-crawler')}
        self.scalers = {'test-crawler': {'metadata': {'labels': {'app.kubernetes.io/instance': 'test'}, 'annotations': {}},
                                       'status': {'conditions': [{'type': 'Paused', 'status': 'True'}]}}}
        self.changes = []
        self.running = []

    def deployment(self, name, patch=None):
        if patch:
            self.changes.append(('deployment', name, patch))
            self.deployments[name]['spec']['replicas'] = patch['spec']['replicas']
        return deepcopy(self.deployments.get(name))

    def scaler(self, name, patch=None):
        if patch:
            self.changes.append(('scaler', name, patch))
            annotations = self.scalers[name]['metadata']['annotations']
            for key, value in patch['metadata']['annotations'].items():
                if value is None:
                    annotations.pop(key, None)
                else:
                    annotations[key] = value
        return deepcopy(self.scalers.get(name))

    def pods(self, release):
        return self.running


class UpgradeCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.cluster = Cluster()
        self.targets = {f'test-{role}': {'role': role, 'replicas': 2, 'image': 'new'} for role in ('api', 'crawler')}

    def run_phase(self, phase):
        upgrade.coordinate(self.cluster, phase, 'test', self.targets, 0)

    def test_stop_replay_then_target_release_start_restores_scaling(self):
        self.run_phase('stop')
        self.run_phase('stop')
        self.assertTrue(all(d['spec']['replicas'] == 0 for d in self.cluster.deployments.values()))
        self.assertEqual(self.cluster.changes[0][0], 'scaler')
        self.run_phase('start')
        self.assertTrue(all(d['spec']['replicas'] == 2 for d in self.cluster.deployments.values()))
        self.assertEqual(self.cluster.scalers['test-crawler']['metadata']['annotations'], {})
        self.run_phase('start')

    def test_terminating_old_pod_blocks_setup(self):
        self.cluster.running = [{'metadata': {'deletionTimestamp': 'now', 'labels': {'app.kubernetes.io/component': 'crawler'}},
                                 'status': {'phase': 'Running'}}]
        with self.assertRaises(TimeoutError):
            self.run_phase('stop')
        self.assertEqual(self.cluster.deployments['test-api']['spec']['replicas'], 0)
        self.assertEqual(self.cluster.scalers['test-crawler']['metadata']['annotations'][upgrade.PAUSE], '0')

    def test_operator_pause_is_preserved_before_any_mutation(self):
        self.cluster.scalers['test-crawler']['metadata']['annotations'][upgrade.PAUSE] = '3'
        with self.assertRaisesRegex(RuntimeError, 'operator-paused'):
            self.run_phase('stop')
        self.assertEqual(self.cluster.changes, [])

    def test_old_image_cannot_be_started_after_migration(self):
        self.cluster.deployments['test-crawler']['spec']['template']['spec']['containers'][0]['image'] = 'old'
        with self.assertRaisesRegex(RuntimeError, 'target release image'):
            self.run_phase('start')
        self.assertEqual(self.cluster.changes, [])

    def test_foreign_release_cannot_be_modified(self):
        self.cluster.deployments['test-api']['metadata']['labels']['app.kubernetes.io/instance'] = 'other'
        with self.assertRaisesRegex(RuntimeError, 'another release'):
            self.run_phase('stop')
        self.assertEqual(self.cluster.changes, [])

    def test_missing_scaler_does_not_require_keda(self):
        self.cluster.scalers = {}
        self.run_phase('stop')
        self.run_phase('start')

    def test_start_waits_for_new_replica_readiness(self):
        self.cluster.deployments['test-api']['status']['availableReplicas'] = 0
        with self.assertRaises(TimeoutError):
            self.run_phase('start')
