"""Run the query bench locally with ephemeral production reader credentials.

Invoke through homelab's operator wrapper. No infrastructure mutations or secret
files; only a temporary metadata port-forward and a bounded local child process.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit


def kubectl(*args):
    return json.loads(subprocess.check_output(
        ["kubectl", "-n", "applications", *args, "-o", "json"], stderr=subprocess.DEVNULL,
        timeout=20,
    ))


def main():
    # This adapter is intentionally specific to the existing homelab reader contract.
    deployment = kubectl("get", "deployment", "periplus-query")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PERIPLUS_", "AWS_"))}
    secrets = {}
    for container in deployment["spec"]["template"]["spec"]["containers"]:
        for entry in container.get("env", []):
            name = entry["name"]
            if not name.startswith(("PERIPLUS_DUCKLAKE_", "PERIPLUS_DUCKDB_")):
                continue
            if "value" in entry:
                env[name] = entry["value"]
            else:
                ref = entry["valueFrom"]["secretKeyRef"]
                if ref["name"] not in {"postgres-periplus-lake-reader", "s3-periplus-lake-reader"}:
                    raise ValueError("unexpected reader secret reference")
                if ref["name"] not in secrets:
                    secrets[ref["name"]] = kubectl("get", "secret", ref["name"])["data"]
                env[name] = base64.b64decode(secrets[ref["name"]][ref["key"]]).decode()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with tempfile.TemporaryFile() as output:
        forward = subprocess.Popen([
            "kubectl", "-n", "state", "port-forward", "service/postgres-rw",
            f"{port}:5432", "--address", "127.0.0.1",
        ], stdout=output, stderr=output)
        try:
            for _ in range(100):
                if forward.poll() is not None:
                    raise RuntimeError("metadata forward failed")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.1):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                raise TimeoutError("metadata forward unavailable")
            metadata = env["PERIPLUS_DUCKLAKE_METADATA_PATH"]
            prefix = "postgres:" if metadata.startswith("postgres:postgres") else ""
            uri = urlsplit(metadata.removeprefix(prefix) if prefix else metadata)
            if uri.scheme not in {"postgresql", "postgres"} or "@" not in uri.netloc:
                raise ValueError("reader metadata must use a PostgreSQL URI")
            env["PERIPLUS_DUCKLAKE_METADATA_PATH"] = prefix + urlunsplit(uri._replace(
                netloc=uri.netloc.rsplit("@", 1)[0] + f"@127.0.0.1:{port}"))
            package = Path(__file__).resolve().parents[1]
            result = subprocess.run([
                str(package / ".venv/bin/python"), str(package / "scripts/query_benchmark.py"),
                *sys.argv[1:],
            ], cwd=package, env=env, capture_output=True, text=True, timeout=900)
            # Runner stdout contains only safe summaries. Never relay native stderr.
            print(result.stdout, end="")
            if result.returncode:
                print("Production benchmark failed; native stderr withheld")
            return result.returncode
        finally:
            forward.terminate()
            try:
                forward.wait(timeout=5)
            except subprocess.TimeoutExpired:
                forward.kill()
                forward.wait()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Production benchmark setup failed: {type(exc).__name__}")
        raise SystemExit(1) from None
