"""Quality commands using the existing database configuration."""

import argparse
import json
from pathlib import Path

import psycopg

from healthcare_dq.db import ROOT, connect

from . import repository
from .engine import ruleset
from .service import validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("validate")
    run.add_argument("batch", type=int)
    run.add_argument("--ruleset", type=Path, default=ROOT / "config/quality/ruleset-v1.json")
    for command in ("status", "findings"):
        sub.add_parser(command).add_argument("run", type=int)
    args = parser.parse_args()
    try:
        with connect() as conn:
            if args.command == "validate":
                result = validate(conn, args.batch, ruleset(args.ruleset))
            elif args.command == "status":
                result = repository.status(conn, args.run)
            else:
                result = repository.findings(conn, args.run)
    except (OSError, ValueError, psycopg.Error):
        print('{"status":"error","code":"QUALITY_COMMAND_FAILED_CHECK_DURABLE_STATUS"}')
        return 1
    print(json.dumps(result, default=str, sort_keys=True))
    state = result.get("status") if isinstance(result, dict) else None
    return {"busy": 3, "failed": 1}.get(state, 0)


if __name__ == "__main__":
    raise SystemExit(main())
