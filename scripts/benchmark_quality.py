"""Time fresh quality runs against already published batches."""

import argparse
import json
import platform
import resource
from time import perf_counter

from healthcare_dq.db import ROOT, connect
from healthcare_dq.quality.engine import ruleset
from healthcare_dq.quality.service import validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=int)
    args = parser.parse_args()
    config = ruleset(ROOT / "config/quality/ruleset-v1.json")
    started = perf_counter()
    with connect() as conn:
        result = validate(conn, args.batch, config)
    total = perf_counter() - started
    if result["status"] != "completed" or result.get("replayed"):
        parser.error("Use a fresh published snapshot for each trial")
    started = perf_counter()
    with connect() as conn:
        replay = validate(conn, args.batch, config)
    replay_seconds = perf_counter() - started
    if not replay.get("replayed") or replay["id"] != result["id"] or replay["counts"] != result["counts"]:
        parser.error("Replay did not match the published result")
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps({
        **result, "total_seconds": total, "replay_seconds": replay_seconds,
        "resources_per_second": result["resources_scanned"] / total,
        "peak_rss_bytes": rss if platform.system() == "Darwin" else rss * 1024,
    }, default=str, sort_keys=True))


if __name__ == "__main__":
    main()
