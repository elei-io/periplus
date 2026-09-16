"""Run bounded materialization probes in a disposable existing Periplus image."""

from __future__ import annotations

import argparse
import json
import subprocess
from hashlib import sha256
from pathlib import Path

from client import ARTIFACTS, DATABASE, HOMELAB

POD = "periplus-access-bench-" + DATABASE.removeprefix("bench_access_").replace(
    "_", "-"
)
ROOT = Path(__file__).resolve().parent


def operator(
    arguments: list[str], *, content: str | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "uv",
            "run",
            "--locked",
            "python",
            "scripts/operator.py",
            "kubectl",
            *arguments,
        ],
        cwd=HOMELAB,
        input=content,
        text=True,
        capture_output=True,
        check=True,
    )


def create() -> None:
    deployment = json.loads(
        operator(
            [
                "get",
                "deployment",
                "periplus-ingestor",
                "-n",
                "applications",
                "-o",
                "json",
            ]
        ).stdout
    )
    container = next(
        c
        for c in deployment["spec"]["template"]["spec"]["containers"]
        if c["name"] == "ingestor"
    )
    allowed = ("PERIPLUS_CLICKHOUSE_", "PERIPLUS_REPOSITORY_")
    environment = [
        item for item in container.get("env", []) if item["name"].startswith(allowed)
    ]
    environment.append({"name": "PERIPLUS_BENCH_DATABASE", "value": DATABASE})
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": POD,
            "namespace": "applications",
            "labels": {
                "app.kubernetes.io/name": "periplus-access-bench",
                "app.kubernetes.io/instance": "periplus",
                "app.kubernetes.io/component": "access-bench",
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "bench",
                    "image": container["image"],
                    "command": ["python", "-c", "import time; time.sleep(10800)"],
                    "env": environment,
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "256Mi"},
                        "limits": {"cpu": "1", "memory": "1Gi"},
                    },
                }
            ],
        },
    }
    print(operator(["create", "-f", "-"], content=json.dumps(pod)).stdout)
    print(
        operator(
            [
                "wait",
                "-n",
                "applications",
                "pod/" + POD,
                "--for=condition=Ready",
                "--timeout=60s",
            ]
        ).stdout
    )


def run(
    program: str,
    low: int = 0,
    high: int = 200,
    table: str = "elements_full_lean",
    batch_mib: int = 96,
) -> None:
    if program not in {"worker", "parser", "raw"}:
        raise ValueError("Unknown probe")
    manifest = {}
    for name in dict.fromkeys(["worker_probe.py", program + "_probe.py"]):
        source = (ROOT / name).read_text()
        manifest[name] = sha256(source.encode()).hexdigest()
        code = "from pathlib import Path; import sys; p=Path('/tmp/periplus-access-paths')/sys.argv[1]; p.parent.mkdir(exist_ok=True); p.write_bytes(sys.stdin.buffer.read())"
        operator(
            ["exec", "-i", "-n", "applications", POD, "--", "python", "-c", code, name],
            content=source,
        )
    arguments = ["python", f"/tmp/periplus-access-paths/{program}_probe.py"]
    if program == "worker":
        arguments.extend([str(low), str(high), table, str(batch_mib)])
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    name = f"worker-{table}-{high}" if program == "worker" else program + "-probe"
    (ARTIFACTS / (name + "-sources.json")).write_text(json.dumps(manifest, indent=2))
    with (ARTIFACTS / (name + ".jsonl")).open("w") as output:
        result = subprocess.run(
            [
                "uv",
                "run",
                "--locked",
                "python",
                "scripts/operator.py",
                "kubectl",
                "exec",
                "-n",
                "applications",
                POD,
                "--",
                *arguments,
            ],
            cwd=HOMELAB,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if result.returncode:
        raise RuntimeError(
            f"Probe failed; inspect {name}.jsonl and partial target before replay"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["create", "worker", "parser", "raw", "delete"]
    )
    parser.add_argument("--after", type=int, default=0)
    parser.add_argument("--scale", type=int, default=200)
    parser.add_argument(
        "--table",
        choices=["elements_full", "elements_full_lean"],
        default="elements_full_lean",
    )
    parser.add_argument("--batch-mib", type=int, choices=[32, 96], default=96)
    args = parser.parse_args()
    if args.action == "create":
        create()
    elif args.action == "delete":
        print(
            operator(
                ["delete", "pod", POD, "-n", "applications", "--wait=false"]
            ).stdout
        )
    else:
        run(args.action, args.after, args.scale, args.table, args.batch_mib)


if __name__ == "__main__":
    main()
