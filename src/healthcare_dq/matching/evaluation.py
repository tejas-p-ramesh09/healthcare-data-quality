"""Offline truth joins and split-locked linkage comparison."""

from collections import Counter


def metrics(tp, fp, fn, tn):
    def ratio(a, b):
        return a / b if b else None
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
            "f1": ratio(2 * tp, 2 * tp + fp + fn), "false_match_rate": ratio(fp, fp + tn),
            "false_suggestion_rate": ratio(fp, tp + fp), "miss_rate": ratio(fn, tp + fn)}


def report(truth, decisions):
    left, right = truth["left"], truth["right"]
    reverse = {person: rid for rid, person in right.items()}
    positives = {(lid, reverse[person]) for lid, person in left.items() if person in reverse}
    total = len(left) * len(right)
    candidates = set(decisions)
    suggested = {k for k, v in decisions.items() if v[0] == "suggested_match"}
    tp, fp = len(suggested & positives), len(suggested - positives)
    present = positives & candidates
    family = {}
    for lid, rid in sorted(positives):
        group = family.setdefault(truth["families"][left[lid]], Counter())
        group["support"] += 1
        group[decisions.get((lid, rid), ("blocked_out",))[0]] += 1
    outcomes = Counter(v[0] for v in decisions.values())
    return {
        "full": metrics(tp, fp, len(positives) - tp, total - len(positives) - fp),
        "candidate_only": metrics(tp, fp, len(present) - tp, len(candidates) - len(present) - fp),
        "candidate_count": len(candidates), "possible_pairs": total,
        "candidate_reduction": 1 - len(candidates) / total if total else None,
        "blocking_recall": len(present) / len(positives) if positives else None,
        "blocked_positives": len(positives - candidates), "dispositions": dict(outcomes),
        "families": family,
        "examples": {
            "missed_families": sorted({truth["families"][left[lid]] for lid, _ in positives - suggested}),
            "false_suggestion_hard_negative_count": sum(
                (l, r) in suggested for l, r, _ in truth["hard_negatives"]),
        },
        "hard_negatives": dict(Counter(decisions.get((l, r), ("blocked_out",))[0]
                                      for l, r, _ in truth["hard_negatives"])),
        "unmatched_left": len(set(left.values()) - set(right.values())),
        "unmatched_right": len(set(right.values()) - set(left.values())),
        "unmatched_suggested_left": len({l for l, _ in suggested if left[l] not in reverse}),
        "unmatched_suggested_right": len({r for _, r in suggested if right[r] not in set(left.values())}),
        "no_candidate_left": len(left.keys() - {l for l, _ in candidates}),
        "no_candidate_right": len(right.keys() - {r for _, r in candidates}),
    }
