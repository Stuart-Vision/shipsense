"""Command line: python -m shipsense.run FILE [FILE ...]

Exit code 0 if every file loaded (or was already loaded), 1 if any failed,
so a scheduler (cron, Airflow) can alert on failures.
"""

import argparse
import json
import sys
from pathlib import Path

from .pipeline import connect, init_schema, process_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load raw ship sensor files into PostgreSQL.")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--database-url", help="defaults to the DATABASE_URL environment variable")
    args = parser.parse_args(argv)

    failed = False
    with connect(args.database_url) as conn:
        init_schema(conn)
        for path in args.files:
            result = process_file(path, conn)
            print(json.dumps(result, default=str))
            failed |= result["status"] == "failed"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
