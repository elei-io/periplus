"""Internal deployment commands for Atlas repository storage."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from repository.objects.config import ensure_s3_bucket_from_env


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m repository")
    parser.add_argument(
        "command",
        choices=("init-s3",),
        help="Initialize explicitly configured repository infrastructure.",
    )
    arguments = parser.parse_args(argv)
    if arguments.command == "init-s3":
        bucket = ensure_s3_bucket_from_env()
        print(f"Repository S3 bucket is ready: {bucket}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
