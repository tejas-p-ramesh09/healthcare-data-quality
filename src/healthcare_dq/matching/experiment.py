"""Seeded, evaluator-only cohorts derived from synthetic Patient records."""

import argparse
import hashlib
import json
import random
from collections import Counter
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid5

import simplejson

from healthcare_dq.fhir import prepare
from healthcare_dq.inputs import MAX_FILES, discover, manifest

from .core import features

NAMESPACE = UUID("b7077063-d724-49e9-a105-963d64f45387")
FAMILIES = ("format", "typo", "surname", "partial_birth", "contact_change", "combined")


def uid(value):
    return str(uuid5(NAMESPACE, value))


def parents(directory):
    files = discover(directory)
    patients = {e.identifier: e.payload for e in prepare(files) if e.kind == "Patient"}
    return manifest(files), patients


def variant(record, family):
    name = record["name"][0]
    match family:
        case "format":
            name["family"] = "  " + name["family"].upper() + " "
        case "typo":
            given = name["given"][0]
            name["given"][0] = given[:-2] + given[-2:][::-1] if len(given) > 2 else given + "x"
        case "surname":
            for item in record["name"]:
                item["family"] = "Variant" + item["family"]
        case "partial_birth":
            record["birthDate"] = record["birthDate"][:4]
        case "contact_change":
            record.pop("telecom", None)
            record.pop("address", None)
        case "combined":
            record.pop("birthDate", None)
            record.pop("telecom", None)
            name["given"][0] = "Other" + name["given"][0]
        case "unmatched":
            pass
        case _:
            raise ValueError("UNKNOWN_VARIATION")


def write_bundles(directory, rows):
    directory.mkdir()
    size = 25
    if (len(rows) + size - 1) // size > MAX_FILES:
        raise ValueError("TOO_MANY_BUNDLES")
    for start in range(0, len(rows), size):
        bundle = {"resourceType": "Bundle", "type": "collection",
                  "entry": [{"resource": p} for p in rows[start:start + size]]}
        (directory / f"bundle-{start // size:04}.json").write_text(
            simplejson.dumps(bundle, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    return manifest(discover(directory))


def build(source, output, seed):
    source = source.resolve()
    output = output.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("SOURCE_OUTPUT_OVERLAP")
    original, patients = parents(source)
    ids = sorted(patients)
    signatures = {(x.names, x.birth) for p in patients.values() if (x := features(p))}
    if len(ids) < 300 or len(ids) != len(signatures):
        raise ValueError("INSUFFICIENT_OR_AMBIGUOUS_PARENT_IDENTITIES")
    rng = random.Random(seed)
    rng.shuffle(ids)
    # Connected hard-negative groups are formed before any split assignment.
    groups = [ids[i:i + 2] for i in range(0, len(ids), 2)]
    n = len(groups)
    segments = (groups[:n * 3 // 5], groups[n * 3 // 5:n * 4 // 5], groups[n * 4 // 5:])
    output.mkdir()
    summary = {"version": 1, "seed": seed, "source_manifest": original, "splits": {}}
    for split, group_set in zip(("fit", "select", "test"), segments, strict=True):
        # Interleave matched and unmatched groups. Every hard negative has both sides represented.
        root = output / split
        root.mkdir()
        sides = {"left": [], "right": []}
        truth = {"left": {}, "right": {}, "families": {}, "hard_negatives": []}
        match_count = 0
        for i, group in enumerate(group_set):
            hard = len(group) == 2 and i % 4 == 0
            matched = i % 5 < 3
            for j, parent in enumerate(group):
                person = uid(f"{seed}/truth/{parent}")
                family = FAMILIES[match_count % len(FAMILIES)] if matched else "unmatched"
                match_count += matched
                truth["families"][person] = family
                for side, records in sides.items():
                    if not matched and side != ("left" if j == 0 else "right"):
                        continue
                    p = deepcopy(patients[parent])
                    p = {k: p[k] for k in ("resourceType", "name", "birthDate", "address", "telecom") if k in p}
                    p["id"] = uid(f"{seed}/{split}/{side}/{parent}")
                    if side == "right":
                        variant(p, family)
                    if hard:
                        p["telecom"] = [{"system": "phone", "value": f"202555{1000 + i:04}"}]
                    records.append(p)
                    truth[side][p["id"]] = person
            if hard:
                for lid, lp in truth["left"].items():
                    for rid, rp in truth["right"].items():
                        if lp != rp and {lp, rp} == {uid(f"{seed}/truth/{p}") for p in group}:
                            truth["hard_negatives"].append((lid, rid, "shared_phone"))
        if split == "test" and (match_count < 30 or sum(map(len, group_set)) - match_count < 20):
            raise ValueError("FINAL_SUPPORT_TOO_LOW")
        digests = {}
        for side, records in sides.items():
            rng.shuffle(records)
            digests[side] = write_bundles(root / side, records)
        truth["manifests"] = digests
        data = json.dumps(truth, sort_keys=True, separators=(",", ":")) + "\n"
        (root / "truth.json").write_text(data, encoding="utf-8")
        summary["splits"][split] = {"matched": match_count, "unmatched": sum(map(len, group_set)) - match_count,
                                    "records": {s: len(v) for s, v in sides.items()},
                                    "hard_negatives": len(truth["hard_negatives"]),
                                    "families": dict(Counter(truth["families"].values())),
                                    "truth_sha256": hashlib.sha256(data.encode()).hexdigest()}
    if manifest(discover(source)) != original:
        raise ValueError("SOURCE_CHANGED")
    (output / "manifest.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def benchmark_inputs(source, output, size):
    source, output = source.resolve(), output.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("SOURCE_OUTPUT_OVERLAP")
    source_hash, patients = parents(source)
    if not 0 < size <= len(patients):
        raise ValueError("INSUFFICIENT_BENCHMARK_INPUT")
    output.mkdir()
    results = {"source_manifest": source_hash}
    for side in ("left", "right"):
        rows = []
        for i, key in enumerate(sorted(patients)[:size]):
            patient = deepcopy(patients[key])
            patient = {k: patient[k] for k in ("resourceType", "name", "birthDate", "address", "telecom")
                       if k in patient}
            patient["id"] = uid(f"benchmark/{size}/{side}/{key}")
            if side == "right":
                variant(patient, FAMILIES[i % len(FAMILIES)])
            rows.append(patient)
        results[side] = write_bundles(output / side, rows)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=303103)
    parser.add_argument("--benchmark-size", type=int)
    args = parser.parse_args()
    result = (build(args.source, args.output, args.seed) if args.benchmark_size is None
              else benchmark_inputs(args.source, args.output, args.benchmark_size))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
