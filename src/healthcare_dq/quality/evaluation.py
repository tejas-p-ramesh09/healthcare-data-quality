"""Score explicit labels, keeping incidental and baseline findings separate."""

import hashlib
import json
from collections import Counter

from .rules import RULES

KEYS = ("rule_id", "rule_version", "path", "entry_index", "pointer")


def load_manifest(path, expected_sha256):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("Frozen manifest checksum mismatch")
    return json.loads(data)


def key(row):
    return tuple(row[k] for k in KEYS)


def ratio(n, d):
    return n / d if d else None


def metrics(tp, fp, fn, tn):
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "support": tp + fn,
            "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
            "f1": ratio(2 * tp, 2 * tp + fp + fn)}


def score(labels, baseline, variant):
    truth = {key(row): row["positive"] for row in labels}
    before = {key(row) for row in baseline}
    after = {key(row) for row in variant}
    if len(truth) != len(labels) or len(before) != len(baseline) or len(after) != len(variant):
        raise ValueError("Duplicate evaluation keys")
    counts = {r.code: Counter() for r in RULES}
    for item, positive in truth.items():
        counts[item[0]][("tp" if item in after else "fn") if positive
                        else ("fp" if item in after else "tn")] += 1

    def get(c):
        return metrics(*(c[k] for k in ("tp", "fp", "fn", "tn")))

    by_category = {}
    for rule in RULES:
        by_category.setdefault(rule.category, Counter()).update(counts[rule.code])
    return {
        "baseline_findings": len(before), "variant_findings": len(after),
        "unchanged_baseline": len(before & after), "baseline_only": sorted(before - after),
        "new_unscored": sorted(after - before - truth.keys()),
        "false_positives": sorted(item for item, label in truth.items() if not label and item in after),
        "missed_positives": sorted(item for item, label in truth.items() if label and item not in after),
        "cohort_baseline_findings": sorted(before & truth.keys()),
        "cohort_size": len(truth), "by_rule": {r: get(c) for r, c in counts.items()},
        "by_category": {r: get(c) for r, c in by_category.items()},
        "micro": get(sum(counts.values(), Counter())),
        "injected_recovery": {r: {"detected": c["tp"], "injected": c["tp"] + c["fn"]}
                              for r, c in counts.items()},
    }
