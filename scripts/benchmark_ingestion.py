"""Measure one fresh database ingestion and replay; emits a JSON record."""

import argparse
import json
import platform
import resource
from pathlib import Path
from time import perf_counter

from healthcare_dq.db import connect, migrate
from healthcare_dq.ingestion import ingest
from healthcare_dq.inputs import discover


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    with connect("MIGRATION_DATABASE_URL") as conn:
        migrate(conn)
        if conn.execute("SELECT EXISTS(SELECT FROM ingestion_batch)").fetchone()[0]:
            parser.error("Use a fresh benchmark database")
    start = perf_counter()
    files = discover(args.directory)
    with connect() as conn:
        first = ingest(conn, "benchmark", files)
    seconds = perf_counter() - start
    if first["status"] != "published":
        parser.error(f"Publication failed: {first['attempts']}")
    start = perf_counter()
    with connect() as conn:
        repeat = ingest(conn, "benchmark", discover(args.directory))
    replay_seconds = perf_counter() - start
    if not repeat.get("replayed") or repeat["batch_id"] != first["batch_id"]:
        parser.error("Replay did not return the committed batch")

    with connect() as conn:
        conn.execute("ANALYZE")
        batch = first["batch_id"]
        person, = conn.execute("SELECT fhir_id FROM patient WHERE batch_id=%s LIMIT 1", (batch,)).fetchone()
        timeline, = conn.execute("""
            EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT fhir_id,effective_at FROM observation WHERE batch_id=%s AND patient_id=%s
            ORDER BY effective_at
        """, (batch, person)).fetchone()
        audit, = conn.execute("""
            EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT f.sha256,r.path,r.entry_index FROM raw_resource r
            JOIN ingestion_file f USING(batch_id,path)
            WHERE r.batch_id=%s AND r.resource_type='Patient' AND r.fhir_id=%s
        """, (batch, person)).fetchone()
        size, = conn.execute("SELECT pg_database_size(current_database())").fetchone()
        server, = conn.execute("SHOW server_version").fetchone()

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps({
        "manifest": first["manifest_sha256"], "counts": first["counts"],
        "input_bytes": sum(len(f.data) for f in files), "publication_elapsed_seconds": seconds,
        "replay_elapsed_seconds": replay_seconds, "stages": first["timing_seconds"],
        "entries_per_second": first["counts"]["raw"] / seconds,
        "peak_rss_bytes": rss if platform.system() == "Darwin" else rss * 1024,
        "database_bytes": size, "postgres": server, "python": platform.python_version(),
        "timeline_plan": timeline, "provenance_plan": audit,
    }))


if __name__ == "__main__":
    main()
