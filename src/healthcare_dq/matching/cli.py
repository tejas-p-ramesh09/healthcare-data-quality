"""Run or inspect cross-source matching recommendations."""

import argparse
import json
from pathlib import Path

import psycopg

from healthcare_dq.db import connect

from .service import no_candidates, run, status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    execute = sub.add_parser("run")
    execute.add_argument("left", type=int)
    execute.add_argument("right", type=int)
    execute.add_argument("model", type=Path)
    for name in ("status", "no-candidates", "pairs"):
        sub.add_parser(name).add_argument("id", type=int)
    args = parser.parse_args()
    try:
        with connect() as conn:
            if args.command == "run":
                result = run(conn, args.left, args.right, json.loads(args.model.read_text())["model"])
            elif args.command == "status":
                result = status(conn, args.id)
            elif args.command == "no-candidates":
                result = [{"side": s, "batch_id": b, "fhir_id": i, "outcome": "no_candidate"}
                          for s, b, i in no_candidates(conn, args.id)]
            else:
                result = conn.execute("""
                    SELECT c.left_batch,c.left_id,c.right_batch,c.right_id,c.block_codes,
                           s.method,s.score,s.evidence,d.disposition,d.reason
                    FROM matching_run r JOIN matching_candidate c ON c.run_id=r.id
                    JOIN matching_score s USING(run_id,left_id,right_id)
                    JOIN matching_recommendation d USING(run_id,left_id,right_id,method)
                    WHERE r.id=%s AND r.status='completed' ORDER BY c.left_id,c.right_id,s.method
                """, (args.id,)).fetchall()
    except (OSError, ValueError, KeyError, psycopg.Error):
        print('{"status":"error","code":"CHECK_DURABLE_MATCHING_STATUS"}')
        return 1
    print(json.dumps(result, allow_nan=False))
    return {"failed": 1, "busy": 3}.get(result.get("status"), 0) if isinstance(result, dict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
