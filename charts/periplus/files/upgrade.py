"""Helm's bounded stop/migrate/start boundary for this Periplus release only."""
import json
import os
from pathlib import Path
import ssl
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PAUSE = "autoscaling.keda.sh/paused-replicas"
OWNER = "periplus.dev/upgrade-pause"


class Kubernetes:
    def __init__(self):
        directory = Path('/var/run/secrets/kubernetes.io/serviceaccount')
        self.token = directory / 'token'
        self.context = ssl.create_default_context(cafile=str(directory / 'ca.crt'))
        self.namespace = os.environ['NAMESPACE']

    def request(self, path, patch=None):
        request = Request('https://kubernetes.default.svc' + path,
                          data=json.dumps(patch).encode() if patch is not None else None,
                          headers={'Authorization': 'Bearer ' + self.token.read_text().strip(),
                                   'Content-Type': 'application/merge-patch+json'},
                          method='PATCH' if patch is not None else 'GET')
        try:
            with urlopen(request, context=self.context, timeout=10) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code == 404 and patch is None:
                return None
            raise RuntimeError(f'Kubernetes {request.method} failed: HTTP {error.code}') from None

    def deployment(self, name, patch=None):
        return self.request(f'/apis/apps/v1/namespaces/{self.namespace}/deployments/{name}', patch)

    def scaler(self, name, patch=None):
        return self.request(f'/apis/keda.sh/v1alpha1/namespaces/{self.namespace}/scaledobjects/{name}', patch)

    def pods(self, release):
        query = urlencode({'labelSelector': f'app.kubernetes.io/instance={release}'})
        return self.request(f'/api/v1/namespaces/{self.namespace}/pods?{query}')['items']


def owned(resource, release):
    if resource and resource['metadata'].get('labels', {}).get('app.kubernetes.io/instance') != release:
        raise RuntimeError('Refusing to change a resource owned by another release')
    return resource


def wait_for(check, timeout):
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() >= deadline:
            raise TimeoutError('Upgrade coordination timed out')
        time.sleep(2)


def coordinate(api, mode, release, targets, timeout):
    # Validate every target before making any changes. A separately paused scaler
    # belongs to the operator and must not be silently resumed by a release.
    deployments = {name: owned(api.deployment(name), release) for name in targets}
    scalers = {name: owned(api.scaler(name), release) for name in targets}
    for scaler in scalers.values():
        annotations = (scaler or {}).get('metadata', {}).get('annotations', {})
        if (PAUSE in annotations or annotations.get('autoscaling.keda.sh/paused') == 'true') and annotations.get(OWNER) != 'true':
            raise RuntimeError('An operator-paused scaler requires operator resolution before upgrading')
    if mode == 'stop':
        for name, scaler in scalers.items():
            if scaler:
                api.scaler(name, {'metadata': {'annotations': {PAUSE: '0', OWNER: 'true'}}})
        def scalers_paused():
            return all(any(c['type'] == 'Paused' and c['status'] == 'True'
                           for c in api.scaler(name).get('status', {}).get('conditions', []))
                       for name, scaler in scalers.items() if scaler)
        wait_for(scalers_paused, timeout)
        for name, deployment in deployments.items():
            if deployment:
                api.deployment(name, {'spec': {'replicas': 0}})
        roles = {target['role'] for target in targets.values()}
        def stopped():
            for name, existing in deployments.items():
                if existing:
                    deployment = api.deployment(name)
                    status = deployment.get('status', {})
                    if (deployment['spec']['replicas'] != 0
                            or status.get('observedGeneration', 0) < deployment['metadata']['generation']
                            or status.get('replicas', 0) != 0):
                        return False
            pods = api.pods(release)
            return not any(p['metadata'].get('labels', {}).get('app.kubernetes.io/component') in roles
                           and p.get('status', {}).get('phase') not in ('Succeeded', 'Failed') for p in pods)
        wait_for(stopped, timeout)
        print('All previous runtime pods drained; setup may run', flush=True)
    elif mode == 'start':
        for name, deployment in deployments.items():
            if not deployment or deployment['spec']['template']['spec']['containers'][0]['image'] != targets[name]['image']:
                raise RuntimeError('Refusing to start a deployment without the target release image')
        for name, target in targets.items():
            api.deployment(name, {'spec': {'replicas': target['replicas']}})
        for name, scaler in scalers.items():
            if scaler and scaler['metadata'].get('annotations', {}).get(OWNER) == 'true':
                api.scaler(name, {'metadata': {'annotations': {PAUSE: None, OWNER: None}}})
        def ready():
            for name in targets:
                deployment = api.deployment(name)
                status = deployment.get('status', {})
                replicas = deployment['spec']['replicas']
                if (status.get('observedGeneration', 0) < deployment['metadata']['generation']
                        or status.get('updatedReplicas', 0) < replicas
                        or status.get('availableReplicas', 0) < replicas):
                    return False
            return True
        wait_for(ready, timeout)
        print('Target release available; autoscaling resumed', flush=True)
    else:
        raise ValueError('Unknown upgrade phase')


if __name__ == '__main__':
    coordinate(Kubernetes(), os.environ['PHASE'], os.environ['RELEASE'],
               json.loads(os.environ['TARGETS']), int(os.environ['TIMEOUT_SECONDS']))
