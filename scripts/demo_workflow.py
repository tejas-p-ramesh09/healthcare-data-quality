"""Run and verify the fixture-only workflow against a local API."""

import argparse
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    token = os.environ["WORKFLOW_TOKEN"]
    expected = json.loads((ROOT / "fixtures/demo/expected.json").read_text())

    def call(path, method="GET", body=None, **headers):
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(f"http://127.0.0.1:{args.port}" + path, data=data, method=method,
                          headers={"Authorization": "Bearer " + token, **headers})
        try:
            with urlopen(request, timeout=45) as result:
                return result.status, json.load(result)
        except HTTPError as error:
            return error.code, json.load(error)

    def checked(path, method="GET", body=None, **headers):
        code, data = call(path, method, body, **headers)
        if code not in (200, 201):
            raise SystemExit(f"HTTP {code}: {data.get('error', {}).get('code', 'UNKNOWN')}")
        return data

    start = time.perf_counter()
    checked("/health/ready")
    request = {"left_fixture": "demo-source-a", "right_fixture": "demo-source-b", "profile": "demo-v1"}
    summary = checked("/v1/workflows", "POST", request, **{"Idempotency-Key": "checked-demo-v1"})
    path = f"/v1/workflows/{summary['id']}"
    timings = []
    for _ in range(15):
        if summary["status"] == "completed":
            break
        tick = time.perf_counter()
        try:
            code, current = call(path + "/advance", "POST")
        except (TimeoutError, URLError):
            summary = checked(path)
            continue
        if code in (409, 503) and current.get("error", {}).get("retryable"):
            time.sleep(.2)
            summary = checked(path)
            continue
        if code != 200:
            raise SystemExit(f"Advance failed: HTTP {code}")
        summary = current
        timings.append(round(time.perf_counter() - tick, 6))
    assert summary["status"] == "completed", summary
    counts = summary["stages"][-1]["counts"]
    for key in ("candidates", "no_candidate_left", "no_candidate_right"):
        assert counts[key] == expected[key], key
    for side, amount in expected["findings"].items():
        found = checked(path + f"/quality-findings?side={side}")["items"]
        batch = checked(path + f"/batches/{side}")
        assert batch["manifest_sha256"] == expected["manifests"][batch["source_key"]]
        labels = checked(path + f"/batches/{side}/files")["items"]
        assert len(found) == amount and len(labels) == 1
        assert all(row["file_sha256"] == labels[0]["sha256"] for row in found)
    for method in ("exact", "statistical"):
        rows = checked(path + f"/recommendations?method={method}")["items"]
        assert sum(r["disposition"] == "suggested_match" for r in rows) == expected[method + "_suggestions"]
        assert rows[0]["evidence"]["levels"]["given"] == "exact"
    tick = time.perf_counter()
    same = checked("/v1/workflows", "POST", request, **{"Idempotency-Key": "checked-demo-v1"})
    assert same == summary
    print(json.dumps({"workflow_id": summary["id"], "status": summary["status"], "counts": counts,
                      "stage_seconds": timings, "replay_seconds": round(time.perf_counter() - tick, 6),
                      "total_seconds": round(time.perf_counter() - start, 6)}, indent=2))


if __name__ == "__main__":
    main()
