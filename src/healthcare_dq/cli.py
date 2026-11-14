"""Command-line entry point for synthetic FHIR ingestion."""

import argparse
import json
import sys
import time
from pathlib import Path

import psycopg

from .db import connect, migrate
from .ingestion import ingest, status
from .inputs import InputError, discover


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("status").add_argument("batch_id", type=int)
    for name in ("ingest", "retry"):
        command = commands.add_parser(name)
        command.add_argument("directory", type=Path)
        command.add_argument("--source", required=True)
        if name == "retry":
            command.add_argument("--batch-id", type=int, required=True)
    args = parser.parse_args()
    start = time.perf_counter()
    try:
        if args.command == "migrate":
            with connect("MIGRATION_DATABASE_URL") as conn:
                migrate(conn)
            result = {"status": "migrated"}
        elif args.command == "status":
            with connect() as conn:
                result = status(conn, args.batch_id)
        else:
            if not args.source or len(args.source) > 128 or not args.source.isascii()                     or any(not (c.isalnum() or c in "._-") for c in args.source):
                raise InputError("INVALID_SOURCE_KEY")
            files = discover(args.directory)
            with connect() as conn:
                result = ingest(conn, args.source, files, retry_batch=getattr(args, "batch_id", None))
    except InputError as error:
        result = {"status": "failed", "code": error.code}
    except (psycopg.Error, OSError, ValueError, OverflowError):
        result = {"status": "error", "code": "COMMAND_FAILED_CHECK_DURABLE_STATUS"}
    print(json.dumps(result, sort_keys=True))
    print(json.dumps({"event": "ingestion_cli_result", "command": args.command,
                      "status": result["status"], "batch_id": result.get("batch_id"),
                      "counts": result.get("counts"), "replayed": result.get("replayed", False),
                      "duration_ms": round((time.perf_counter() - start) * 1000, 3)}), file=sys.stderr)
    if result["status"] == "busy":
        return 3
    return 1 if result["status"] in {"failed", "error"} else 0


if __name__ == "__main__":
    sys.exit(main())
