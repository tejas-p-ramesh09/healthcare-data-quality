"""Benchmark already published matching inputs and replay."""

import argparse
import json
import platform
import resource
from pathlib import Path
from time import perf_counter

from healthcare_dq.db import connect
from healthcare_dq.matching.service import run


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("left", type=int)
    p.add_argument("right", type=int)
    p.add_argument("model", type=Path)
    args = p.parse_args()
    model = json.loads(args.model.read_text())["model"]
    start = perf_counter()
    with connect() as conn:
        out = run(conn, args.left, args.right, model)
    elapsed = perf_counter() - start
    if out["status"] != "completed" or out.get("replayed"):
        p.error("Fresh published inputs required")
    start = perf_counter()
    with connect() as conn:
        replay = run(conn, args.left, args.right, model)
    if not replay.get("replayed") or replay["id"] != out["id"]:
        p.error("Replay mismatch")
    replay_seconds = perf_counter() - start
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    counts = out["counts"]
    print(json.dumps({**out, "elapsed": elapsed, "replay": replay_seconds,
                      "peak_rss_bytes": rss if platform.system() == "Darwin" else rss * 1024,
                      "comparisons_per_second": counts["candidates"] / out["seconds"]["score"],
                      "reduction": 1 - counts["candidates"] / (counts["left"] * counts["right"])}))


if __name__ == "__main__":
    main()
