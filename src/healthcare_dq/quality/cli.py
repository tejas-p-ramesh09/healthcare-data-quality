"""Quality commands using the existing database configuration."""

import argparse
import json
from pathlib import Path

import psycopg

from healthcare_dq.db import ROOT, connect

from . import repository
from .engine import digest, ruleset
from .evaluation import score
from .mutation import freeze
from .service import validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("validate")
    run.add_argument("batch", type=int)
    run.add_argument("--ruleset", type=Path, default=ROOT / "config/quality/ruleset-v1.json")
    for command in ("status", "findings"):
        sub.add_parser(command).add_argument("run", type=int)
    mutate = sub.add_parser("mutate")
    mutate.add_argument("source", type=Path)
    mutate.add_argument("destination", type=Path)
    mutate.add_argument("--experiment", type=Path, default=ROOT / "config/quality/experiment-v1.json")
    evaluation = sub.add_parser("evaluate")
    evaluation.add_argument("manifest", type=Path)
    evaluation.add_argument("baseline", type=int)
    evaluation.add_argument("variant", type=int)
    args = parser.parse_args()
    try:
        if args.command == "mutate":
            settings = json.loads(args.experiment.read_text())
            result = freeze(args.source, args.destination, settings, ruleset(ROOT / settings["ruleset"]))
            print(json.dumps(result, sort_keys=True))
            return 0
        with connect() as conn:
            if args.command == "validate":
                result = validate(conn, args.batch, ruleset(args.ruleset))
            elif args.command == "status":
                result = repository.status(conn, args.run)
            elif args.command == "findings":
                result = repository.findings(conn, args.run)
            else:
                experiment = json.loads(args.manifest.read_text())
                runs = [repository.status(conn, rid) for rid in (args.baseline, args.variant)]
                if (runs[0]["engine_revision"] != runs[1]["engine_revision"]
                    or any(run["status"] != "completed"
                           or run["ruleset_sha256"] != experiment["ruleset_sha256"] for run in runs)):
                    raise ValueError("Incompatible validation runs")
                for run, name in zip(runs, ("source_manifest", "variant_manifest"), strict=True):
                    actual, = conn.execute("SELECT manifest_sha256 FROM ingestion_batch WHERE id=%s",
                                           (run["batch_id"],)).fetchone()
                    if actual != experiment[name]:
                        raise ValueError("Manifest does not match validation batch")
                result = score(experiment["labels"], *[repository.findings(conn, r["id"]) for r in runs])
                result.update({"manifest_digest": digest(experiment),
                               "baseline_run": args.baseline, "variant_run": args.variant,
                               "engine_revision": runs[0]["engine_revision"]})
    except (OSError, ValueError, psycopg.Error):
        print('{"status":"error","code":"QUALITY_COMMAND_FAILED_CHECK_DURABLE_STATUS"}')
        return 1
    print(json.dumps(result, default=str, sort_keys=True))
    state = result.get("status") if isinstance(result, dict) else None
    return {"busy": 3, "failed": 1}.get(state, 0)


if __name__ == "__main__":
    raise SystemExit(main())
