"""Fit and freeze a matcher before running the isolated final split."""

import argparse
import hashlib
import json
from pathlib import Path

from healthcare_dq.matching.core import features
from healthcare_dq.matching.experiment import parents
from healthcare_dq.matching.pipeline import assess, select


def load(root, split, index):
    data = (root / split / "truth.json").read_bytes()
    if hashlib.sha256(data).hexdigest() != index["splits"][split]["truth_sha256"]:
        raise ValueError("TRUTH_DIGEST_MISMATCH")
    truth = json.loads(data)
    sides = []
    for side in ("left", "right"):
        checksum, rows = parents(root / split / side)
        if checksum != truth["manifests"][side] or rows.keys() != truth[side].keys():
            raise ValueError("COHORT_MISMATCH")
        if len(set(truth[side].values())) != len(rows):
            raise ValueError("DUPLICATE_PARENT_ON_SIDE")
        sides.append({i: features(p) for i, p in rows.items()})
    return *sides, truth


def save(path, data):
    with path.open("x", encoding="utf-8") as f:
        f.write(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("fit", "test"))
    p.add_argument("cohort", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    manifest_bytes = (a.cohort / "manifest.json").read_bytes()
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    index = json.loads(manifest_bytes)
    if a.command == "fit":
        fit_data, selection = (load(a.cohort, k, index) for k in ("fit", "select"))
        model, results = select(fit_data, selection)
        save(a.output, {"cohort_sha256": digest, "model": model, **results})
    else:
        artifact = json.loads((a.output / "fit.json").read_text())
        if artifact["cohort_sha256"] != digest:
            raise ValueError("FIT_COHORT_MISMATCH")
        splits = [load(a.cohort, k, index) for k in ("fit", "select", "test")]
        ids = [set(t["left"].values()) | set(t["right"].values()) for _, _, t in splits]
        if any(a & b for i, a in enumerate(ids) for b in ids[i + 1:]):
            raise ValueError("IDENTITY_LEAKAGE")
        model = artifact["model"]
        result = assess(*splits[-1], model)
        save(a.output / "test.json", {"cohort_sha256": digest, "model": model, "final": result})


if __name__ == "__main__":
    main()
