"""Atlas direct-HTTP acquisition worker entrypoint."""

import argparse
import asyncio

from workers.acquisition import run


def main() -> None:
    argparse.ArgumentParser(description="Run the Atlas HTTP acquisition worker.").parse_args()
    asyncio.run(run("http"))


if __name__ == "__main__":
    main()
