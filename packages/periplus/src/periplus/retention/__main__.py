"""Read-only bounded review: uv run python -m periplus.retention."""
import argparse
import json

from periplus.platform.postgres.session import SessionLocal
from periplus.retention.runtime import RetentionSettings, RetentionSweep


def main():
    parser = argparse.ArgumentParser(description='Review one bounded retention batch without deleting anything.')
    parser.add_argument('--limit', type=int, default=25)
    args = parser.parse_args()
    settings = RetentionSettings.from_env().model_copy(update={'mode': 'dry_run', 'batch_size': args.limit})
    settings = RetentionSettings.model_validate(settings.model_dump())
    print(json.dumps(RetentionSweep(settings, SessionLocal).run(), indent=2))


if __name__ == '__main__':
    main()
