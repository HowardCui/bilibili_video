"""在可终止的子进程中执行一次排行榜采集。"""

import argparse
from pathlib import Path

from app_logging import configure_logging
from ranking_collector.config import DATABASE_PATH
from ranking_collector.service import collect_once


def parse_arguments(arguments=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    return parser.parse_args(arguments)


def main(arguments=None):
    options = parse_arguments(arguments)
    configure_logging("ranking")
    result = collect_once(database_path=options.database)
    return 0 if result["succeeded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
