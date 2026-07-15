"""Atlas external-provider acquisition worker entrypoint."""

import argparse
import asyncio

from workers.acquisition import run


def main() -> None:
    argparse.ArgumentParser(description="Run the Atlas provider acquisition worker.").parse_args()
    asyncio.run(run("firecrawl"))


if __name__ == "__main__":
    main()
