"""Read-only TrueNAS telemetry; run through homelab's credential wrapper.

The internal appliance uses a self-signed TLS certificate. No API key, login
reply, application configuration or unrelated events are written to results.
"""

import argparse
import json
import os
from pathlib import Path
import ssl
import time

from websockets.sync.client import connect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="wss://192.168.0.101/api/current")
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with connect(args.url, ssl=ssl._create_unverified_context(), open_timeout=10) as ws:

        def call(identity, method, params):
            ws.send(
                json.dumps(
                    dict(jsonrpc="2.0", id=identity, method=method, params=params)
                )
            )
            while True:
                result = json.loads(ws.recv(timeout=20))
                if result.get("id") == identity:
                    if "error" in result:
                        raise RuntimeError("TrueNAS read-only telemetry call failed")
                    return result["result"]

        assert call(1, "auth.login_with_api_key", [os.environ["TRUENAS_API_KEY"]])
        call(2, "core.subscribe", ['reporting.realtime:{"interval":2}'])
        start = time.monotonic()
        count = 0
        with args.output.open("a") as output:
            while time.monotonic() - start < args.seconds:
                event = json.loads(ws.recv(timeout=20))
                params = event.get("params", {})
                fields = params.get("fields", {})
                if not fields:
                    # JSON-RPC notification envelope varies by API version.
                    fields = params.get("data", {}).get("fields", {})
                if "disks" not in fields:
                    continue
                record = dict(
                    time=time.time(),
                    cpu=fields.get("cpu", {}).get("cpu"),
                    disks=fields["disks"],
                    memory=fields.get("memory"),
                    zfs=fields.get("zfs"),
                )
                output.write(json.dumps(record) + "\n")
                output.flush()
                count += 1
        print(json.dumps(dict(samples=count, seconds=time.monotonic() - start)))


if __name__ == "__main__":
    main()
